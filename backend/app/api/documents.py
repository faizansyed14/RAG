import asyncio
import hashlib
import json
import logging
import uuid
from collections import defaultdict

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.config import get_settings
from app.core.object_store import get_object_store
from app.core.ratelimit import limit_user
from app.core.upload_validation import CONTENT_TYPES, extension_of, read_capped, sanitize_filename, validate_content
from app.core.security import require_admin, require_user
from app.ingestion.progress import subscribe, unsubscribe
from app.ingestion.router import ingest_document
from app.models.db import DiagramPage, Document, DocumentFolder, Folder, get_session
from app.models.schemas import DocumentOut, DocumentTreeResponse, SetDocumentFoldersRequest, UploadResponse
from app.rag_service import delete_document as rag_delete_document, get_index_dump, get_tree
from app.retrieval.vector_store import delete_by_document, diagram_collection_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])

_ALLOWED_TYPES = {
    "pdf": "pdf", "docx": "docx", "csv": "csv", "xlsx": "xlsx", "eml": "eml",
    "txt": "txt", "json": "json", "xer": "xer",
}


async def _folder_ids_by_document(session: AsyncSession, document_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[uuid.UUID]]:
    """One grouped query for however many documents are being returned,
    not one query per document."""
    if not document_ids:
        return {}
    rows = (
        await session.execute(
            select(DocumentFolder.document_id, DocumentFolder.folder_id).where(
                DocumentFolder.document_id.in_(document_ids)
            )
        )
    ).all()
    grouped: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for document_id, folder_id in rows:
        grouped[document_id].append(folder_id)
    return grouped


def _document_out(doc: Document, folder_ids: list[uuid.UUID]) -> DocumentOut:
    out = DocumentOut.model_validate(doc)
    out.folder_ids = folder_ids
    return out


async def _add_to_folder(session: AsyncSession, document_id: uuid.UUID, folder_id: uuid.UUID) -> None:
    existing = await session.get(DocumentFolder, {"document_id": document_id, "folder_id": folder_id})
    if existing is not None:
        return
    session.add(DocumentFolder(document_id=document_id, folder_id=folder_id))
    await session.commit()


@router.post("", response_model=UploadResponse, dependencies=[Depends(limit_user("upload", "rl_upload_per_hour", 3600))])
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile,
    folder_id: uuid.UUID | None = Form(None),
    admin=Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> UploadResponse:
    filename = sanitize_filename(file.filename)
    ext = extension_of(filename)
    if ext not in _ALLOWED_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unsupported file type: .{ext} (pdf, docx, csv, xlsx, eml, txt, json, or xer only)",
        )
    doc_type = _ALLOWED_TYPES[ext]

    if folder_id is not None and await session.get(Folder, folder_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")

    data = await read_capped(file, get_settings().max_upload_mb * 1024 * 1024)
    validate_content(ext, data)
    content_hash = hashlib.sha256(data).hexdigest()

    existing = await session.execute(select(Document).where(Document.content_hash == content_hash))
    existing_doc = existing.scalar_one_or_none()
    if existing_doc is not None:
        # Re-uploading (possibly identical) content into a folder still
        # files it there -- e.g. dragging an already-indexed file into a
        # second folder via the OS file picker should work like "copy",
        # not silently do nothing.
        if folder_id is not None:
            await _add_to_folder(session, existing_doc.document_id, folder_id)
        return UploadResponse(document_id=existing_doc.document_id, status=existing_doc.status)

    store = get_object_store()
    storage_key = f"originals/{uuid.uuid4()}/{filename}"
    store.put_object(storage_key, data, content_type=CONTENT_TYPES[ext])

    doc = Document(filename=filename, doc_type=doc_type, content_hash=content_hash,
                    storage_key=storage_key, status="queued")
    session.add(doc)
    await session.commit()
    await session.refresh(doc)

    if folder_id is not None:
        await _add_to_folder(session, doc.document_id, folder_id)

    audit("document_uploaded", by=admin.username, document=doc.document_id, type=doc_type, bytes=len(data))
    background_tasks.add_task(ingest_document, doc.document_id, storage_key, doc_type)
    return UploadResponse(document_id=doc.document_id, status="queued")


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    folder_id: uuid.UUID | None = None,
    unfiled: bool = False,
    _admin: str = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> list[DocumentOut]:
    query = select(Document).order_by(Document.created_at.desc())
    if folder_id is not None:
        query = query.join(DocumentFolder, DocumentFolder.document_id == Document.document_id).where(
            DocumentFolder.folder_id == folder_id
        )
    elif unfiled:
        query = query.outerjoin(DocumentFolder, DocumentFolder.document_id == Document.document_id).where(
            DocumentFolder.folder_id.is_(None)
        )
    docs = (await session.execute(query)).scalars().all()
    folder_map = await _folder_ids_by_document(session, [d.document_id for d in docs])
    return [_document_out(d, folder_map.get(d.document_id, [])) for d in docs]


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: uuid.UUID,
    _admin: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> DocumentOut:
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    folder_map = await _folder_ids_by_document(session, [document_id])
    return _document_out(doc, folder_map.get(document_id, []))


@router.put("/{document_id}/folders", response_model=DocumentOut)
async def set_document_folders(
    document_id: uuid.UUID,
    body: SetDocumentFoldersRequest,
    _admin: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> DocumentOut:
    """Replaces a document's complete folder membership in one call -- the
    one primitive that drives move (new set = [target]), copy (new set =
    current + [target]), and remove-from-folder (new set = current minus
    one) from the frontend, which computes the desired final set itself."""
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    folder_ids = list(dict.fromkeys(body.folder_ids))  # de-dupe, keep order
    if folder_ids:
        found = (
            await session.execute(select(Folder.folder_id).where(Folder.folder_id.in_(folder_ids)))
        ).scalars().all()
        missing = set(folder_ids) - set(found)
        if missing:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Folder(s) not found: {', '.join(str(m) for m in missing)}")

    await session.execute(delete(DocumentFolder).where(DocumentFolder.document_id == document_id))
    for folder_id in folder_ids:
        session.add(DocumentFolder(document_id=document_id, folder_id=folder_id))
    await session.commit()

    return _document_out(doc, folder_ids)


@router.get("/{document_id}/tree", response_model=DocumentTreeResponse)
async def get_document_tree(
    document_id: uuid.UUID,
    _admin: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> DocumentTreeResponse:
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    if doc.rag_doc_id is None:
        return DocumentTreeResponse(document_id=document_id, tree=[])
    tree = await asyncio.to_thread(get_tree, doc.rag_doc_id)
    return DocumentTreeResponse(document_id=document_id, tree=tree.get("result", []))


@router.get("/{document_id}/progress")
async def document_progress(
    document_id: uuid.UUID,
    _admin: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """SSE stream of ingestion phases as they happen -- see ingestion/progress.py.

    progress.py's pub/sub has no history: publish() only reaches queues that
    already exist at that exact moment, so subscribing after the terminal
    event already fired means waiting forever on one that will never repeat.
    That's not just a narrow race -- it's guaranteed whenever upload_document's
    content-hash dedup path returns an already-indexed document without
    scheduling a new ingest_document() task at all (no background task means
    no publish() will ever fire for this document_id again). Subscribing
    before checking status (not after) closes the narrow race; checking
    status at all closes the dedup case.
    """
    queue = subscribe(document_id)
    doc = await session.get(Document, document_id)
    already_done = doc is not None and doc.status in ("indexed", "failed")

    async def event_stream():
        try:
            if already_done:
                event: dict = {"phase": doc.status}
                if doc.status == "failed" and doc.error:
                    event["error"] = doc.error
                yield f"data: {json.dumps(event)}\n\n".encode("utf-8")
                return
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event)}\n\n".encode("utf-8")
                if event.get("phase") in ("indexed", "failed"):
                    break
        finally:
            unsubscribe(document_id, queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/{document_id}/raw")
async def get_document_raw(
    document_id: uuid.UUID,
    _admin: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Full stored index dump: Postgres row, object-store keys, tree-engine
    meta/pages/tree, and any diagram OCR rows — so the UI can show exactly
    what was persisted after upload."""
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    diagrams = (
        await session.execute(
            select(DiagramPage)
            .where(DiagramPage.document_id == document_id)
            .order_by(DiagramPage.page_number)
        )
    ).scalars().all()

    index: dict = {
        "rag_doc_id": doc.rag_doc_id,
        "meta": None,
        "pages": [],
        "node_chunks": [],
        "tree": [],
    }
    if doc.rag_doc_id:
        try:
            index = await asyncio.to_thread(get_index_dump, doc.rag_doc_id)
        except Exception as exc:  # noqa: BLE001 -- surface to UI, don't 500 the dump
            index = {
                "rag_doc_id": doc.rag_doc_id,
                "meta": None,
                "pages": [],
                "node_chunks": [],
                "tree": [],
                "error": str(exc),
            }

    return {
        "document": {
            "document_id": str(doc.document_id),
            "filename": doc.filename,
            "doc_type": doc.doc_type,
            "status": doc.status,
            "status_detail": doc.status_detail,
            "error": doc.error,
            "is_scanned": doc.is_scanned,
            "page_count": doc.page_count,
            "content_hash": doc.content_hash,
            "storage_key": doc.storage_key,
            "preview_storage_key": doc.preview_storage_key,
            "rag_doc_id": doc.rag_doc_id,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
        },
        "index": index,
        "diagrams": [
            {
                "page_number": d.page_number,
                "figure_id": d.figure_id,
                "caption": d.caption,
                "description": d.description,
                "ocr_text": d.ocr_text,
                "image_key": d.image_key,
                "qdrant_point_id": str(d.qdrant_point_id),
                "embedding_model": d.embedding_model,
                "callouts": d.callouts,
            }
            for d in diagrams
        ],
    }


@router.get("/{document_id}/preview")
async def preview_document(
    document_id: uuid.UUID,
    _admin: str = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    # DOCX: preview the rendered PDF actually fed to the tree engine (a raw
    # .docx can't be rendered by the frontend's PDF viewer) -- see
    # ingestion/docx_ingest.py. PDF: the original is already a PDF.
    key = doc.preview_storage_key or doc.storage_key
    url = get_object_store().presigned_url(key)
    return {"url": url}


@router.get("/{document_id}/diagrams/{page_number}")
async def preview_diagram_page(
    document_id: uuid.UUID,
    page_number: int,
    _admin: str = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    result = await session.execute(
        select(DiagramPage).where(
            DiagramPage.document_id == document_id, DiagramPage.page_number == page_number
        )
    )
    diagram = result.scalar_one_or_none()
    if diagram is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Diagram page not found")
    url = get_object_store().presigned_url(diagram.image_key)
    return {"url": url, "caption": diagram.caption, "description": diagram.description,
            "figure_id": diagram.figure_id, "ocr_text": diagram.ocr_text}


def _delete_object_logged(key: str, what: str, document_id: uuid.UUID) -> None:
    try:
        get_object_store().delete_object(key)
    except Exception:
        logger.exception("Failed to delete %s (%s) for document %s", what, key, document_id)


@router.delete("/{document_id}")
async def delete_document(
    document_id: uuid.UUID,
    _admin: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    # Diagram rows carry the only pointers to their page-crop PNGs -- read
    # them before the DELETE below removes those pointers, or the objects
    # become permanently unreachable orphans in object storage.
    diagram_keys = (
        await session.execute(
            select(DiagramPage.image_key).where(DiagramPage.document_id == document_id)
        )
    ).scalars().all()

    if doc.rag_doc_id:
        try:
            await asyncio.to_thread(rag_delete_document, doc.rag_doc_id)
        except Exception:
            logger.exception("Failed to delete tree-engine document %s for document %s", doc.rag_doc_id, document_id)

    _delete_object_logged(doc.storage_key, "original upload", document_id)
    if doc.preview_storage_key:
        _delete_object_logged(doc.preview_storage_key, "preview", document_id)
    for image_key in diagram_keys:
        _delete_object_logged(image_key, "diagram page image", document_id)

    await delete_by_document(diagram_collection_name(), document_id)
    await session.execute(delete(DiagramPage).where(DiagramPage.document_id == document_id))
    await session.delete(doc)
    await session.commit()
    return {"document_id": str(document_id), "deleted": True}
