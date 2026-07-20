"""Step 2 — Extraction.

Turn a tender PDF into clean, structured text the LLM can read, WITHOUT OCR
when a text layer exists. Strategy decided empirically (GeM bids carry a full
text layer; values are English; Hindi labels are font-garbled noise):

  1. PyMuPDF  -> per-page text (the key/value "form" body)
  2. pdfplumber -> per-page tables (specs table, consignee table)
  3. strip Devanagari -> remove the garbled bilingual label half
  4. detect scanned pages (chars/page < threshold) -> flag for OCR fallback

Output: data/output/<name>_extracted.json  (+ a .txt preview for eyeballing)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import pdfplumber

# Allow running as a script (python src/extract.py) or as a module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

# U+0900–U+097F Devanagari, plus zero-width joiners that ride along with it.
_DEVANAGARI = re.compile(r"[ऀ-ॿ‌‍]")
# Unmapped glyphs from GeM's custom font surface as "(cid:NN)" — pure noise.
_CID = re.compile(r"\(cid:\d+\)")
_WS_RUN = re.compile(r"[ \t]{2,}")
_BLANK_LINES = re.compile(r"\n{3,}")


def strip_hindi(text: str) -> str:
    """Drop Devanagari and font artifacts, then tidy the leftover whitespace."""
    if not text:
        return ""
    text = _DEVANAGARI.sub("", text)
    text = _CID.sub("", text)
    # Collapse runs of spaces and clean each line's edges.
    lines = [_WS_RUN.sub(" ", ln).strip() for ln in text.split("\n")]
    text = "\n".join(lines)
    return _BLANK_LINES.sub("\n\n", text).strip()


def _clean_cell(cell: Any) -> str:
    return strip_hindi(str(cell)) if cell is not None else ""


def _extract_tables(plumber_page) -> list[list[list[str]]]:
    """Return tables as cleaned list-of-rows; drop empty/degenerate ones."""
    tables: list[list[list[str]]] = []
    try:
        raw_tables = plumber_page.extract_tables() or []
    except Exception:
        return tables
    for tbl in raw_tables:
        rows = [[_clean_cell(c) for c in row] for row in tbl]
        rows = [r for r in rows if any(cell for cell in r)]  # drop blank rows
        if len(rows) >= 2:  # need at least a header + a row to be a real table
            tables.append(rows)
    return tables


def _table_to_text(table: list[list[str]]) -> str:
    return "\n".join(" | ".join(cell for cell in row) for row in table)


def extract_tender(pdf_path: str | Path) -> dict:
    pdf_path = Path(pdf_path)
    doc = fitz.open(pdf_path)
    plumber = pdfplumber.open(pdf_path)

    page_count = doc.page_count
    pages: list[dict] = []
    scanned_pages: list[int] = []
    try:
        for i in range(page_count):
            raw_text = doc[i].get_text("text")
            clean_text = strip_hindi(raw_text)
            tables = _extract_tables(plumber.pages[i])

            is_scanned = len(raw_text.strip()) < config.SCANNED_CHARS_PER_PAGE
            if is_scanned:
                scanned_pages.append(i + 1)

            pages.append({
                "page": i + 1,
                "scanned": is_scanned,
                "text": clean_text,
                "tables": tables,
            })
    finally:
        doc.close()
        plumber.close()

    # One concatenated, LLM-ready document: text + tables rendered inline.
    blocks: list[str] = []
    for p in pages:
        blocks.append(f"### PAGE {p['page']}")
        if p["scanned"]:
            blocks.append("[scanned page — no text layer; OCR fallback needed]")
        if p["text"]:
            blocks.append(p["text"])
        for j, tbl in enumerate(p["tables"], 1):
            blocks.append(f"\n[TABLE {j} | page {p['page']}]\n{_table_to_text(tbl)}")
        blocks.append("")
    rich_text = "\n".join(blocks).strip()

    return {
        "source_pdf": pdf_path.name,
        "page_count": page_count,
        "scanned_pages": scanned_pages,
        "needs_ocr": len(scanned_pages) > 0,
        "pages": pages,
        "rich_text": rich_text,
    }


def main(pdf_path: str | Path) -> dict:
    result = extract_tender(pdf_path)
    stem = Path(pdf_path).stem
    json_out = config.OUTPUT_DIR / f"{stem}_extracted.json"
    txt_out = config.OUTPUT_DIR / f"{stem}_extracted.txt"

    json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    txt_out.write_text(result["rich_text"], encoding="utf-8")

    n_tables = sum(len(p["tables"]) for p in result["pages"])
    print(f"Extracted {result['source_pdf']}: {result['page_count']} pages, "
          f"{n_tables} tables, {len(result['rich_text'])} chars.")
    if result["needs_ocr"]:
        print(f"  ⚠ scanned pages needing OCR: {result['scanned_pages']}")
    print(f"  -> {json_out.name}")
    print(f"  -> {txt_out.name}")
    return result


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else config.RAW_DIR / "fpga_8663118.pdf"
    main(target)
