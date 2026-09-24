import json

from app.ingestion.json_ingest import extract_blocks


def _make_json(tmp_path, content):
    path = str(tmp_path / "data.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def test_extract_blocks_pretty_prints_valid_json(tmp_path):
    path = _make_json(tmp_path, json.dumps({"drawing_no": "A-108", "revision": "A"}))
    blocks = extract_blocks(path)

    assert blocks[0] == {"type": "heading", "text": "data"}
    assert blocks[1]["type"] == "code"
    assert json.loads(blocks[1]["text"]) == {"drawing_no": "A-108", "revision": "A"}
    assert "\n" in blocks[1]["text"]  # actually pretty-printed, not minified


def test_extract_blocks_falls_back_to_raw_text_on_invalid_json(tmp_path):
    path = _make_json(tmp_path, "{not valid json,,,")
    blocks = extract_blocks(path)

    assert blocks[1]["text"] == "{not valid json,,,"


def test_extract_blocks_empty_file(tmp_path):
    path = _make_json(tmp_path, "")
    assert extract_blocks(path) == []
