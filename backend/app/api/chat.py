import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.guardrails import sanitize_history, screen_query
from app.core.netutil import client_ip
from app.core.quota import Quota, QuotaExceeded, refund, reserve, snapshot
from app.core.ratelimit import acquire_chat_lease, limit_user, release_chat_lease, throttled
from app.core.security import CurrentUser, get_current_user
from app.models.db import Document, User, async_session, get_session
from app.models.schemas import ChatRequest
from app.retrieval.chat_service import stream_chat

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

_MAX_HISTORY_MESSAGES = 40
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _sse(event_type: str, payload: dict) -> bytes:
    return f"event: {event_type}\ndata: {json.dumps(payload)}\n\n".encode("utf-8")


async def _refund_message(user_id: uuid.UUID) -> Quota | None:
    try:
        async with async_session() as session:
            return await refund(session, user_id)
    except Exception:
        log.exception("Could not refund credits for user %s", user_id)
        return None


async def _current_quota(user_id: uuid.UUID) -> Quota | None:
    async with async_session() as session:
        user = await session.get(User, user_id)
        return snapshot(user) if user else None


async def _canned_stream(reply: str, user: CurrentUser):
    """A refusal/greeting sent through the normal event shapes. It never touches
    the model and never costs credits."""
    yield _sse("answer", {"type": "answer", "delta": reply})
    yield _sse(
        "citations",
        {"type": "citations", "citations": [], "diagrams": [], "resolved_answer": reply, "refused": True},
    )
    if user.role != "admin":
        quota = await _current_quota(user.user_id)
        if quota is not None:
            yield _sse("usage", {"type": "usage", "quota": quota.to_dict()})
    yield _sse("done", {"type": "done"})


@router.post("", dependencies=[Depends(limit_user("chat", "rl_chat_per_minute", 60))])
async def chat(
    body: ChatRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    # Layer 1: cheap screen before anything is spent -- no credits, no model call.
    verdict, canned = screen_query(body.query)
    if canned is not None:
        audit("chat_screened", user=user.username, verdict=verdict, ip=client_ip(request))
        return StreamingResponse(_canned_stream(canned, user), media_type="text/event-stream", headers=_SSE_HEADERS)

    query = select(Document.document_id, Document.rag_doc_id).where(
        Document.status == "indexed", Document.rag_doc_id.is_not(None)
    )
    if body.document_ids:
        query = query.where(Document.document_id.in_(body.document_ids))
    rows = (await session.execute(query)).all()
    if not rows:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No indexed documents to search yet")

    rag_doc_ids = [row.rag_doc_id for row in rows]
    document_ids = [row.document_id for row in rows]
    # Citations from the tree engine only carry its own doc id ("pi-...");
    # the frontend needs our document_id to fetch a preview, so it's
    # attached here rather than making the frontend maintain its own map.
    document_id_by_rag_id = {row.rag_doc_id: str(row.document_id) for row in rows}
    # Also map by filename for citations that only carry a document name.
    name_rows = (
        await session.execute(
            select(Document.document_id, Document.filename, Document.rag_doc_id).where(
                Document.status == "indexed", Document.rag_doc_id.is_not(None)
            )
        )
    ).all()
    if body.document_ids:
        id_set = {str(i) for i in body.document_ids}
        name_rows = [r for r in name_rows if str(r.document_id) in id_set]
    document_id_by_name = {r.filename: str(r.document_id) for r in name_rows}

    # Prior turns come from the browser, so they are untrusted (layer 2).
    history = sanitize_history(
        [{"role": m.role, "content": m.content} for m in body.history[-_MAX_HISTORY_MESSAGES:]]
    )

    # One answer at a time per user; the lease expires on its own if a worker dies.
    if not await acquire_chat_lease(user.user_id):
        raise throttled(5, "Your previous message is still being answered. Please wait for it to finish.")

    # Charged up front and only after the request is known to be valid, so a
    # 400 above never costs credits. Admins are not metered.
    metered = user.role != "admin"
    if metered:
        try:
            await reserve(session, user.user_id)
        except QuotaExceeded as exc:
            await release_chat_lease(user.user_id)
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "limit_reached",
                    "message": "You've reached your usage limit.",
                    "retry_after_seconds": exc.quota.retry_after_seconds,
                    "quota": exc.quota.to_dict(),
                },
                headers={"Retry-After": str(exc.quota.retry_after_seconds)},
            ) from exc
        except Exception:
            await release_chat_lease(user.user_id)
            raise

    async def event_stream():
        produced_answer = False
        errored = False
        refused = False
        settled = False
        try:
            async for event in stream_chat(body.query, rag_doc_ids, document_ids, history):
                event_type = event.get("type")
                if event_type == "answer" and event.get("delta"):
                    produced_answer = True
                elif event_type == "error":
                    errored = True
                elif event_type == "citations":
                    refused = bool(event.get("refused"))
                    for citation in event.get("citations", []):
                        doc_id = document_id_by_rag_id.get(citation.get("doc_id"))
                        if not doc_id:
                            doc_id = document_id_by_name.get(citation.get("document") or "")
                        citation["document_id"] = doc_id
                elif event_type == "done" and metered:
                    # Failed before answering, or refused by the guardrails -> free;
                    # otherwise the charge stands.
                    if refused or (errored and not produced_answer):
                        if refused:
                            audit("chat_refused", user=user.username, ip=client_ip(request))
                        quota = await _refund_message(user.user_id)
                    else:
                        quota = await _current_quota(user.user_id)
                    settled = True
                    if quota is not None:
                        yield _sse("usage", {"type": "usage", "quota": quota.to_dict()})
                yield _sse(event_type or "message", event)
        finally:
            # Client disconnected or the engine raised before `done`: no answer, no charge.
            if metered and not settled and not produced_answer:
                await asyncio.shield(_refund_message(user.user_id))
            await asyncio.shield(release_chat_lease(user.user_id))

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)
