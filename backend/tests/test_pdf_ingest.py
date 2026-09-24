import fitz  # PyMuPDF, used only to synthesize test fixtures

from app.ingestion.pdf_ingest import classify_pdf, has_text_layer, page_count


def _make_pdf(tmp_path, text: str | None):
    doc = fitz.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 72), text)
    path = str(tmp_path / "test.pdf")
    doc.save(path)
    doc.close()
    return path


def test_has_text_layer_true_for_text_pdf(tmp_path):
    path = _make_pdf(tmp_path, "This drawing set contains general arrangement plans " * 5)
    assert has_text_layer(path) is True


def test_has_text_layer_false_for_blank_pdf(tmp_path):
    path = _make_pdf(tmp_path, None)
    assert has_text_layer(path) is False


def test_classify_pdf_scanned_flag(tmp_path):
    scanned_path = _make_pdf(tmp_path, None)
    assert classify_pdf(scanned_path)["is_scanned"] is True

    text_path = _make_pdf(tmp_path, "readable text " * 10)
    assert classify_pdf(text_path)["is_scanned"] is False


def test_page_count(tmp_path):
    path = _make_pdf(tmp_path, "one page")
    assert page_count(path) == 1
