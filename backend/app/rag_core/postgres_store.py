"""Postgres-backed replacement for local_store.py's DocStore -- NOT part of the vendored
engine. Added on top of it (see docs/ARCHITECTURE.md) once the tree/page JSON turned out to
be small (a few KB to low MB per document) and was the one thing forcing this backend to run
as a single instance with no backup story of its own: local_store.py's DocStore wrote it to
loose files on a host-local Docker volume. Confirmed DocStore is the *only* thing in the
vendored engine that touches those files, so this is a narrow storage-backend swap -- nothing
about local_api.py's behavior changes, only where doc.json/tree.json/pages.json actually live.

Duck-type compatible with DocStore's interface (get_meta, get_tree, get_pages, list_metas,
save_document, delete_document, lock) -- see local_api.py:42, the one line that switches
between them. local_store.py/DocStore are left untouched and unused, not deleted, so that
swap is a one-line revert if anything here needs to be rolled back.

Synchronous, not async: local_api.py runs off the event loop via asyncio.to_thread() (see
rag_service.py), so this needs a blocking driver -- psycopg2, via its own small SQLAlchemy
engine, separate from models/db.py's async one.
"""

from __future__ import annotations

import json
import zlib
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine, text

from app.core.config import get_settings

# One fixed key for the single global mutex DocStore.lock() provided (confirmed: one call
# site, local_api.py's submit_document, guarding the check-then-write name-uniqueness race --
# not a per-document lock). Any 64-bit int works; this is just a stable, arbitrary constant.
_LOCK_KEY = zlib.crc32(b"rag_core.postgres_store.submit_document") & 0x7FFFFFFF


def _strip_nul(value):
    """Postgres's jsonb type rejects the NUL codepoint (U+0000) even escaped inside a JSON
    string -- confirmed live: a real invoice PDF extracted a stray \\u0000 in place of a
    hyphen ("KPFUIKY8\\u000005" for "KPFUIKY8-0005"), and psycopg2 raised
    UntranslatableCharacter on insert. NUL in extracted document text is always an
    extraction artifact, never meaningful content, so it's dropped -- same spirit as
    local_api.py's own _scrub_surrogates() for lone surrogates."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [_strip_nul(item) for item in value]
    if isinstance(value, dict):
        return {key: _strip_nul(item) for key, item in value.items()}
    return value


def _sync_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+"):
        return dsn
    return dsn.replace("postgresql://", "postgresql+psycopg2://", 1)


@lru_cache
def _engine():
    return create_engine(_sync_dsn(get_settings().postgres_dsn), pool_pre_ping=True)


class PostgresDocStore:
    def save_document(self, doc_id: str, meta: dict, tree: list, pages: list) -> None:
        with _engine().begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO document_trees (doc_id, meta, tree, pages) "
                    "VALUES (:doc_id, CAST(:meta AS jsonb), CAST(:tree AS jsonb), CAST(:pages AS jsonb)) "
                    "ON CONFLICT (doc_id) DO UPDATE SET "
                    "meta = EXCLUDED.meta, tree = EXCLUDED.tree, pages = EXCLUDED.pages, updated_at = now()"
                ),
                {
                    "doc_id": doc_id,
                    "meta": json.dumps(_strip_nul(meta)),
                    "tree": json.dumps(_strip_nul(tree)),
                    "pages": json.dumps(_strip_nul(pages)),
                },
            )

    def get_meta(self, doc_id: str) -> dict | None:
        return self._get_column(doc_id, "meta")

    def get_tree(self, doc_id: str) -> list | None:
        return self._get_column(doc_id, "tree")

    def get_pages(self, doc_id: str) -> list | None:
        return self._get_column(doc_id, "pages")

    def _get_column(self, doc_id: str, column: str):
        # column is always one of the three literal strings above, never user input.
        query = {"meta": "SELECT meta FROM document_trees WHERE doc_id = :doc_id",
                 "tree": "SELECT tree FROM document_trees WHERE doc_id = :doc_id",
                 "pages": "SELECT pages FROM document_trees WHERE doc_id = :doc_id"}[column]
        with _engine().connect() as conn:
            row = conn.execute(text(query), {"doc_id": doc_id}).first()
        return row[0] if row else None

    def list_metas(self) -> list[dict]:
        with _engine().connect() as conn:
            rows = conn.execute(text("SELECT meta FROM document_trees")).all()
        return [row[0] for row in rows]

    def search_metas(self, query: str, limit: int, offset: int,
                      doc_ids=None) -> tuple[list[dict], int]:
        """Keyword (tsvector) ranked search over name/description/top-level section titles --
        see alembic/versions/0008_document_trees_search.py for how search_vector is maintained.
        Lexical, not semantic: no embeddings involved. `doc_ids`, when given, scopes the search
        the same way chat's selected-document scope does (see agent_tools.py's _allowed_ids)."""
        params: dict = {"query": query, "limit": limit, "offset": offset}
        where = "search_vector @@ plainto_tsquery('english', :query)"
        if doc_ids is not None:
            where += " AND doc_id = ANY(:doc_ids)"
            params["doc_ids"] = list(doc_ids)

        with _engine().connect() as conn:
            total = conn.execute(
                text(f"SELECT count(*) FROM document_trees WHERE {where}"), params
            ).scalar_one()
            rows = conn.execute(
                text(
                    f"SELECT meta FROM document_trees WHERE {where} "
                    "ORDER BY ts_rank(search_vector, plainto_tsquery('english', :query)) DESC "
                    "LIMIT :limit OFFSET :offset"
                ),
                params,
            ).all()
        return [row[0] for row in rows], total

    def delete_document(self, doc_id: str) -> bool:
        with _engine().begin() as conn:
            result = conn.execute(
                text("DELETE FROM document_trees WHERE doc_id = :doc_id RETURNING 1"), {"doc_id": doc_id}
            )
            return result.first() is not None

    @contextmanager
    def lock(self):
        with _engine().connect() as conn:
            conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _LOCK_KEY})
            conn.commit()
            try:
                yield
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _LOCK_KEY})
                conn.commit()
