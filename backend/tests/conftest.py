import os

os.environ.setdefault("POSTGRES_DSN", "postgresql://rag:change_me@localhost:5432/rag_engine_test")
os.environ.setdefault("AUTH_SECRET", "test-secret")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "test-password")

# core/config.py's five model_* fields have no Python default (see its
# docstring) -- tests need something set, even though these particular
# values are never actually called out to.
os.environ.setdefault("RAG_INDEX_MODEL", "openai/gpt-4o-mini")
os.environ.setdefault("RAG_CHAT_MODEL", "openai/gpt-4o-mini")
os.environ.setdefault("MODEL_VISION", "qwen/qwen3-vl-235b-a22b-instruct")
os.environ.setdefault("MODEL_EMBEDDING", "openai/text-embedding-3-small")
os.environ.setdefault("MODEL_EMBEDDING_DIMENSION", "1536")


import uuid  # noqa: E402

import pytest_asyncio  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def _fresh_engine_per_test():
    # Both the DB engine's connection pool and get_qdrant_client()'s
    # @lru_cache'd client are process-wide singletons, but pytest-asyncio
    # gives each test its own event loop; a pooled/cached connection left
    # over from a previous test's now-closed loop breaks the moment a later
    # test reuses it. Disposing/clearing first forces fresh clients bound to
    # whichever loop is actually running.
    from app.models.db import _engine
    from app.retrieval.vector_store import get_qdrant_client

    await _engine.dispose()
    get_qdrant_client.cache_clear()
    # Rate-limit counters are keyed by client IP, and every test client is "127.0.0.1":
    # start each test from a clean slate so logins in one test can't throttle the next.
    from sqlalchemy import text

    from app.models.db import async_session

    async with async_session() as session:
        await session.execute(text("DELETE FROM rate_limits"))
        await session.commit()
    yield


@pytest_asyncio.fixture
async def make_user():
    """Factory: creates a real user row and returns (user, auth_headers, password).
    Every user created is deleted again at teardown."""
    from app.core.security import create_token, hash_password
    from app.models.db import User, async_session

    created: list[uuid.UUID] = []

    async def _make(role: str = "user", credit_limit: int = 100, password: str = "s3cret-pass!"):
        async with async_session() as session:
            user = User(
                username=f"t_{uuid.uuid4().hex[:12]}",
                password_hash=hash_password(password),
                role=role,
                credit_limit=credit_limit,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        created.append(user.user_id)
        token = create_token(user.user_id, user.role, user.token_version)
        return user, {"Authorization": f"Bearer {token}"}, password

    yield _make

    async with async_session() as session:
        for user_id in created:
            row = await session.get(User, user_id)
            if row is not None:
                await session.delete(row)
        await session.commit()
