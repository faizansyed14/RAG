"""
PDF classification. A PDF with a real text layer submits straight to the
tree engine. A scanned or drawing-heavy PDF has no text layer for the
engine's local mode to read at all (it refuses a PDF whose pages are all
blank), so it can't be submitted directly -- ingestion/router.py runs the
diagram pipeline first (our own pinned vision model OCRs every page) and
only then builds a tree, from that OCR text, via
ingestion/text_to_pdf.render_ocr_pages_as_pdf + app.rag_service.submit_pdf.
"""

from pypdf import PdfReader

_MIN_CHARS_PER_PAGE = 40


def has_text_layer(file_path: str) -> bool:
    reader = PdfReader(file_path)
    if not reader.pages:
        return False
    sampled = reader.pages[: min(5, len(reader.pages))]
    total_chars = sum(len((p.extract_text() or "").strip()) for p in sampled)
    return (total_chars / len(sampled)) >= _MIN_CHARS_PER_PAGE


def page_count(file_path: str) -> int:
    return len(PdfReader(file_path).pages)


def classify_pdf(file_path: str) -> dict:
    scanned = not has_text_layer(file_path)
    return {"is_scanned": scanned, "page_count": page_count(file_path)}
