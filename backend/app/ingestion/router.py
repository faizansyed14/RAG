"""
Ingestion dispatch, run as a background task after documents.py has
already written the `documents` row and confirmed content_hash is new.
Publishes progress events throughout (ingestion/progress.py) -- this is
what the frontend's real-time ingestion view actually watches.

Tree-engine calls (submit_pdf, get_tree) and DOCX parsing are synchronous,
blocking, LLM-calling operations (see app/rag_service.py) -- run via
asyncio.to_thread so a slow index build never blocks the event loop other
requests (including this same document's own progress SSE stream) depend on.
"""

import asyncio
import logging
import tempfile
import uuid
from pathlib import Path

from app.core.object_store import get_object_store
from app.ingestion import csv_ingest, docx_ingest, eml_ingest, json_ingest, pdf_ingest, txt_ingest, xer_ingest, xlsx_ingest
from app.ingestion.diagram_pipeline import process_pdf_pages
from app.ingestion.progress import publish
from app.ingestion.text_to_pdf import render_ocr_pages_as_pdf
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.models.db import DiagramPage, Document, async_session
from app.rag_service import get_tree, submit_pdf
from app.retrieval.vector_store import delete_by_document, diagram_collection_name

logger = logging.getLogger(__name__)

# Every non-PDF format reduces to the same block-rendering pipeline
# (ingestion/blocks_ingest.py) -- only the extraction differs per format.
_BLOCK_PARSERS = {
    "docx": docx_ingest.parse_docx,
    "csv": csv_ingest.parse_csv,
    "xlsx": xlsx_ingest.parse_xlsx,
    "eml": eml_ingest.parse_eml,
    "txt": txt_ingest.parse_txt,
    "json": json_ingest.parse_json,
    "xer": xer_ingest.parse_xer,
}
_STATUS_DETAIL = {
    "docx": "Extracting paragraphs and tables",
    "csv": "Extracting rows",
    "xlsx": "Extracting sheets",
    "eml": "Extracting message",
    "txt": "Extracting text",
    "json": "Extracting document",
    "xer": "Extracting schedule tables",
}


def _named_pdf_path(tmp_dir: str, original_filename: str) -> str:
    """The tree engine's local mode names a document after the basename of
    the path it's given (rag_core/local_api.py) -- a random tempfile name
    would surface as the citation's document name instead of the real
    uploaded filename. Writing into a fresh per-document temp *directory*
    under the real (extension-swapped-to-.pdf) name keeps citations
    readable without colliding with any other upload's temp file."""
    stem = Path(original_filename).stem or "document"
    return str(Path(tmp_dir) / f"{stem}.pdf")


async def _ingest_pdf(document_id: uuid.UUID, tmp_path: str, session) -> tuple[str, bool, int]:
    classification = await asyncio.to_thread(pdf_ingest.classify_pdf, tmp_path)
    if not classification["is_scanned"]:
        publish(document_id, {"phase": "indexing"})
        result = await asyncio.to_thread(submit_pdf, tmp_path, "flash")
        return result["doc_id"], False, classification["page_count"]

    ocr_pages = await process_pdf_pages(document_id, tmp_path, session)
    publish(document_id, {"phase": "indexing"})
    # A sibling directory so the synthetic PDF can reuse tmp_path's exact
    # basename (same original-derived name -- see _named_pdf_path) without
    # colliding with tmp_path itself, which still exists at this point.
    with tempfile.TemporaryDirectory() as synth_dir:
        synthetic_pdf = str(Path(synth_dir) / Path(tmp_path).name)
        render_ocr_pages_as_pdf(ocr_pages, output_path=synthetic_pdf)
        result = await asyncio.to_thread(submit_pdf, synthetic_pdf, "flash")
    return result["doc_id"], True, classification["page_count"]


# At most `ingest_concurrency` documents are indexed at a time; the rest wait as "queued".
# One semaphore per event loop (a semaphore must not be shared across loops).
_gates: dict[int, asyncio.Semaphore] = {}
_recovery_tasks: set[asyncio.Task] = set()
_UNFINISHED = ("queued", "extracting", "ocr", "indexing")


def _gate() -> asyncio.Semaphore:
    key = id(asyncio.get_running_loop())
    if key not in _gates:
        _gates[key] = asyncio.Semaphore(max(1, get_settings().ingest_concurrency))
    return _gates[key]


async def ingest_document(document_id: uuid.UUID, storage_key: str, doc_type: str) -> None:
    async with _gate():
        await _ingest_document_inner(document_id, storage_key, doc_type)


def schedule_ingest(document_id: uuid.UUID, storage_key: str, doc_type: str) -> None:
    """Start indexing in the background, independent of the HTTP request that asked for it.
    (Starlette BackgroundTasks keep the request -- and its database connection -- alive until
    the job finishes, which exhausts the connection pool as soon as many uploads queue up.)"""
    task = asyncio.create_task(ingest_document(document_id, storage_key, doc_type))
    _recovery_tasks.add(task)
    task.add_done_callback(_recovery_tasks.discard)


async def prepare_retry(session, doc: Document) -> None:
    """Put a failed/interrupted document back to `queued`, discarding the diagram rows and
    vectors a half-finished run may have written so a second run doesn't duplicate them."""
    await session.execute(delete(DiagramPage).where(DiagramPage.document_id == doc.document_id))
    try:
        await delete_by_document(diagram_collection_name(), doc.document_id)
    except Exception:  # noqa: BLE001 -- nothing to clean if the collection doesn't exist yet
        logger.warning("Could not clear diagram vectors for %s before retry", doc.document_id)
    doc.status = "queued"
    doc.status_detail = None
    doc.error = None
    await session.commit()


async def recover_unfinished_ingests(only: set[uuid.UUID] | None = None) -> int:
    """Resume documents a restart left in queued/extracting/ocr/indexing. Runs once at boot
    (single-instance deployments: with several workers, set RECOVER_STUCK_INGESTS=false on all but one)."""
    async with async_session() as session:
        query = select(Document).where(Document.status.in_(_UNFINISHED))
        if only is not None:
            query = query.where(Document.document_id.in_(only))
        docs = (await session.execute(query.order_by(Document.created_at))).scalars().all()
        jobs = []
        for doc in docs:
            await prepare_retry(session, doc)
            jobs.append((doc.document_id, doc.storage_key, doc.doc_type))
    for document_id, storage_key, doc_type in jobs:
        schedule_ingest(document_id, storage_key, doc_type)
    if jobs:
        logger.info("Resuming %d unfinished document(s) after restart", len(jobs))
    return len(jobs)


async def _ingest_document_inner(document_id: uuid.UUID, storage_key: str, doc_type: str) -> None:
    async with async_session() as session:
        doc = await session.get(Document, document_id)
        if doc is None:
            logger.error("ingest_document called for missing document %s", document_id)
            return

        doc.status = "extracting"
        doc.status_detail = "Reading file"
        await session.commit()
        publish(document_id, {"phase": "extracting"})

        store = get_object_store()

        try:
            data = await asyncio.to_thread(store.get_object, storage_key)
            with tempfile.TemporaryDirectory() as tmp_dir:
                if doc_type == "pdf":
                    tmp_path = _named_pdf_path(tmp_dir, doc.filename)
                else:
                    tmp_path = str(Path(tmp_dir) / (Path(doc.filename).name or f"document.{doc_type}"))
                Path(tmp_path).write_bytes(data)

                if doc_type == "pdf":
                    rag_doc_id, is_scanned, page_count = await _ingest_pdf(document_id, tmp_path, session)
                else:
                    doc.status = "extracting"
                    doc.status_detail = _STATUS_DETAIL.get(doc_type, "Extracting content")
                    await session.commit()
                    parser = _BLOCK_PARSERS[doc_type]
                    result = await asyncio.to_thread(parser, tmp_path, document_id)
                    rag_doc_id, is_scanned = result["rag_doc_id"], False
                    page_count = None
                    doc.preview_storage_key = result["preview_storage_key"]

                doc.rag_doc_id = rag_doc_id
                doc.is_scanned = is_scanned
                if page_count is not None:
                    doc.page_count = page_count
                doc.status = "indexed"
                doc.status_detail = None
                await session.commit()

                tree = await asyncio.to_thread(get_tree, rag_doc_id)
                publish(document_id, {"phase": "indexed", "tree": tree.get("result", [])})

        except Exception as exc:
            doc.status = "failed"
            doc.error = str(exc)
            await session.commit()
            publish(document_id, {"phase": "failed", "error": str(exc)})
            logger.exception("Ingestion failed for document %s", document_id)
