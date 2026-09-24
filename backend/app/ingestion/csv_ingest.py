"""
CSV ingestion. The tree engine's local mode only accepts PDF input (see
app/rag_service.py's module docstring), so this reads the sheet into one
grid table block and renders it through the shared block pipeline
(blocks_ingest.py), same as a DOCX table.
"""

import csv
from pathlib import Path


def _sniff_dialect(sample: str) -> type[csv.Dialect]:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.excel


def extract_blocks(file_path: str) -> list[dict]:
    with open(file_path, newline="", encoding="utf-8-sig", errors="replace") as f:
        sample = f.read(4096)
        f.seek(0)
        dialect = _sniff_dialect(sample)
        rows = [row for row in csv.reader(f, dialect) if any(cell.strip() for cell in row)]

    if not rows:
        return []
    return [{"type": "heading", "text": Path(file_path).stem}, {"type": "table", "rows": rows}]


def parse_csv(file_path: str, document_id) -> dict:
    from app.ingestion.blocks_ingest import submit_blocks

    blocks = extract_blocks(file_path)
    return submit_blocks(blocks, file_path, document_id)
