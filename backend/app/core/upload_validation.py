"""Upload hardening: never trust the client's filename, content type, or size.
Everything here is pure and unit-tested; api/documents.py just calls it."""

import io
import json
import re
import unicodedata
import zipfile

from fastapi import HTTPException, UploadFile, status

# The content type stored on the object and served back via presigned URL comes from
# the validated extension, never from the client's header.
CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
    "eml": "message/rfc822",
    "txt": "text/plain",
    "json": "application/json",
    "xer": "text/plain",
}

_MAX_FILENAME = 150
_MAX_ZIP_UNCOMPRESSED = 500 * 1024 * 1024
_MAX_ZIP_RATIO = 200


def _bad(message: str) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, message)


def sanitize_filename(raw: str | None) -> str:
    """Basename only, no control characters or path separators, bounded length,
    extension preserved. Safe for the DB, the storage key and the UI."""
    name = unicodedata.normalize("NFKC", raw or "")
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip().strip(".")
    name = re.sub(r"[^\w.\- ()\[\]]", "_", name)
    if len(name) > _MAX_FILENAME:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: _MAX_FILENAME - len(ext) - 1] + "." + ext) if dot else name[:_MAX_FILENAME]
    return name or "upload"


def extension_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


async def read_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Read the upload in chunks and stop as soon as it exceeds the cap."""
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"File too large (limit {max_bytes // (1024 * 1024)} MB)"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _check_zip(data: bytes, required: tuple[str, ...], label: str) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise _bad(f"That doesn't look like a valid {label} file") from exc
    with archive:
        names = set(archive.namelist())
        if not all(part in names for part in required):
            raise _bad(f"That doesn't look like a valid {label} file")
        infos = archive.infolist()
        total = sum(info.file_size for info in infos)
        if total > _MAX_ZIP_UNCOMPRESSED or total > max(len(data), 1) * _MAX_ZIP_RATIO:
            raise _bad(f"That {label} file expands to an unreasonable size")


def validate_content(ext: str, data: bytes) -> None:
    """Check the bytes really are what the extension claims. Raises HTTP 400."""
    if not data:
        raise _bad("The file is empty")
    if ext == "pdf":
        if b"%PDF-" not in data[:1024]:
            raise _bad("That doesn't look like a valid PDF file")
    elif ext == "docx":
        _check_zip(data, ("[Content_Types].xml", "word/document.xml"), "Word")
    elif ext == "xlsx":
        _check_zip(data, ("[Content_Types].xml", "xl/workbook.xml"), "Excel")
    else:
        if b"\x00" in data[:8192]:
            raise _bad(f"That doesn't look like a valid .{ext} text file")
        head = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
        if ext == "json":
            try:
                json.loads(data.decode("utf-8-sig"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise _bad("That file is not valid JSON") from exc
        elif ext == "xer":
            if not head.startswith(b"ERMHDR"):
                raise _bad("That doesn't look like a valid Primavera XER file")
        elif ext == "eml":
            if not re.search(rb"(?im)^(received|from|to|subject|date|mime-version|message-id):", data[:4096]):
                raise _bad("That doesn't look like a valid email (.eml) file")
