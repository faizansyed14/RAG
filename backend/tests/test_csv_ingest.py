from app.ingestion.csv_ingest import extract_blocks


def _make_csv(tmp_path, text):
    path = str(tmp_path / "test.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(text)
    return path


def test_extract_blocks_comma(tmp_path):
    path = _make_csv(tmp_path, "Drawing No,Revision\nA-108,A\nA-109,B\n")
    blocks = extract_blocks(path)

    assert [b["type"] for b in blocks] == ["heading", "table"]
    assert blocks[0]["text"] == "test"
    assert blocks[1]["rows"] == [["Drawing No", "Revision"], ["A-108", "A"], ["A-109", "B"]]


def test_extract_blocks_semicolon_delimiter(tmp_path):
    path = _make_csv(tmp_path, "Drawing No;Revision\nA-108;A\n")
    blocks = extract_blocks(path)

    assert blocks[1]["rows"] == [["Drawing No", "Revision"], ["A-108", "A"]]


def test_extract_blocks_empty_file(tmp_path):
    path = _make_csv(tmp_path, "")
    assert extract_blocks(path) == []
