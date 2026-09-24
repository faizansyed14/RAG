import asyncio
import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()  # before anything reads env vars at import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import auth, chat, documents, folders, health, users
from app.core.audit import request_id_var
from app.core.bootstrap import ensure_bootstrap_admin
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.middleware import SecurityMiddleware
from app.core.object_store import get_object_store
from app.core.ratelimit import purge_old

configure_logging()
log = logging.getLogger(__name__)


async def _purge_loop() -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            await purge_old()
        except Exception:
            log.exception("rate-limit purge failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    problems = settings.production_problems()
    if settings.is_prod and problems:
        raise RuntimeError("Refusing to start with an insecure production configuration:\n - " + "\n - ".join(problems))

    # Idempotent -- safe to run on every boot. Without this, the first
    # upload after a fresh MinIO/S3 target hits NoSuchBucket, since
    # nothing else creates the bucket before it's needed.
    await asyncio.to_thread(get_object_store().ensure_bucket)
    await ensure_bootstrap_admin()
    purge_task = asyncio.create_task(_purge_loop())
    try:
        yield
    finally:
        purge_task.cancel()


def create_app() -> FastAPI:
    settings = get_settings()
    # The interactive API docs are a map of the attack surface: dev only.
    docs_kwargs = {"docs_url": None, "redoc_url": None, "openapi_url": None} if settings.is_prod else {}
    app = FastAPI(title="RAG Engine API", version="0.1.0", lifespan=lifespan, **docs_kwargs)

    # Added first = innermost, so CORS (added last) also decorates the 413/429 replies.
    app.add_middleware(SecurityMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,  # auth is a bearer header, never a cookie
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["Retry-After", "X-Request-ID"],
        max_age=600,
    )

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return JSONResponse(
            {"detail": "Internal server error", "request_id": request_id_var.get()}, status_code=500
        )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(documents.router)
    app.include_router(folders.router)
    app.include_router(chat.router)
    return app


app = create_app()
