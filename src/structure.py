"""Step 3 — Structuring.

Turn the messy extracted rich_text into a typed tender schema the rest of the
pipeline can rely on:

  metadata     -> for the "is this of interest?" gate (deadline, org, qty)
  eligibility  -> turnover / experience / EMD / required docs
  line_items[] -> {name, quantity, specifications{}}  ← what we match to inventory

Primary path: HuggingFace LLM. Fallback: a deterministic parser that reuses the
tables already extracted in step 2, so a rate-limited HF endpoint never blocks us.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

SCHEMA: dict[str, Any] = {
    "metadata": {
        "title": "",
        "bid_number": "",
        "issuing_organization": "",
        "submission_deadline": "",
        "total_quantity": "",
    },
    "eligibility": {
        "min_avg_annual_turnover": "",
        "oem_turnover": "",
        "experience_years": "",
        "emd_required": "",
        "documents_required": [],
    },
    "line_items": [],  # [{ "name": "", "quantity": "", "specifications": {} }]
}

SYSTEM = "You are a precise procurement analyst. You extract facts and output only valid JSON."

PROMPT = """Extract the tender below into a SINGLE valid JSON object with EXACTLY this shape:

{schema}

Rules:
- Use only facts present in the text. If a field is unknown, use "" (or [] for lists).
- line_items: one object per distinct product/service being procured. Put each
  technical specification as a key/value pair inside "specifications"
  (e.g. "Processor": "Dual A9", "# of logic cells": "256K or higher").
- submission_deadline: the bid end date/time.
- Output ONLY the JSON object. No markdown, no commentary.

TENDER TEXT:
{body}
"""


# ---------- JSON parsing helpers ----------
def _strip_fences(text: str) -> str:
    text = text.strip()
    m = re.search(r"```(?:json)?(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return text


def _outer_json(text: str) -> str:
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else text


def _parse_json(raw: str) -> dict:
    return json.loads(_outer_json(_strip_fences(raw)))


def _coerce(obj: Any, schema: Any) -> Any:
    """Force obj into the schema's shape; fill missing keys with defaults."""
    if isinstance(schema, dict):
        obj = obj if isinstance(obj, dict) else {}
        return {k: _coerce(obj.get(k, v), v) for k, v in schema.items()}
    if isinstance(schema, list):
        return obj if isinstance(obj, list) else []
    return obj if obj is not None else ""


# ---------- Deterministic fallback (reuses step-2 tables) ----------
def _kv_from_text(text: str, label: str) -> str:
    """Grab the value line following a '/<label>' marker in the cleaned text."""
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if label.lower() in ln.lower():
            for nxt in lines[i + 1:i + 3]:
                nxt = nxt.strip()
                if nxt and not nxt.endswith("/" + label) and "/" not in nxt[:2]:
                    return nxt
    return ""


def _specs_from_tables(extracted: dict) -> dict[str, str]:
    """Pull name->allowed-value pairs from the spec table found in step 2."""
    specs: dict[str, str] = {}
    for page in extracted["pages"]:
        for tbl in page["tables"]:
            header = " ".join(tbl[0]).lower()
            if "specification" in header or "bid requirement" in header:
                for row in tbl[1:]:
                    cells = [c for c in row if c.strip()]
                    if len(cells) >= 2:
                        name, value = cells[-2], cells[-1]
                        if name and value and not name.startswith("/"):
                            specs[name] = value
    return specs


def _all_tables_text(extracted: dict) -> str:
    """Render every extracted table — these carry the specs the LLM must see."""
    blocks: list[str] = []
    for p in extracted["pages"]:
        for tbl in p["tables"]:
            rows = "\n".join(" | ".join(c for c in row) for row in tbl)
            blocks.append(f"[TABLE p{p['page']}]\n{rows}")
    return "\n\n".join(blocks)


_NAME_KEYS = ("name", "item_name", "product", "product_name", "title", "item", "Name")
_QTY_KEYS = ("quantity", "qty", "Quantity", "total_quantity")


def _normalize_item(it: Any, fallback_specs: dict[str, str]) -> dict | None:
    """Map the LLM's varied key names to our shape; backfill empty specs."""
    if not isinstance(it, dict):
        return None
    name = next((str(it[k]) for k in _NAME_KEYS if it.get(k)), "")
    qty = next((it[k] for k in _QTY_KEYS if it.get(k) not in (None, "")), "")
    specs = it.get("specifications") or it.get("specs") or {}
    if not isinstance(specs, dict) or not specs:
        specs = dict(fallback_specs)
    return {"name": name or "Unknown item", "quantity": qty, "specifications": specs}


def heuristic_structure(extracted: dict) -> dict:
    text = extracted["rich_text"]
    out = json.loads(json.dumps(SCHEMA))  # deep copy
    item = _kv_from_text(text, "Item Category")
    out["metadata"]["title"] = item or extracted["source_pdf"]
    out["metadata"]["issuing_organization"] = _kv_from_text(text, "Organisation Name")
    out["metadata"]["submission_deadline"] = _kv_from_text(text, "Bid End Date")
    out["metadata"]["total_quantity"] = _kv_from_text(text, "Total Quantity")
    out["eligibility"]["min_avg_annual_turnover"] = _kv_from_text(text, "Minimum Average Annual Turnover")
    out["eligibility"]["oem_turnover"] = _kv_from_text(text, "OEM Average Turnover")
    out["eligibility"]["experience_years"] = _kv_from_text(text, "Years of Past Experience")
    specs = _specs_from_tables(extracted)
    out["line_items"] = [{
        "name": item or "Unknown item",
        "quantity": out["metadata"]["total_quantity"],
        "specifications": specs,
    }]
    return out


# ---------- Main ----------
def _postprocess(result: dict, extracted: dict) -> dict:
    """Shared cleanup for both the LLM and heuristic paths."""
    md = result["metadata"]
    # Backfill blank item names from the tender title / item category.
    title = md.get("title") or _kv_from_text(extracted["rich_text"], "Item Category")
    tq = md.get("total_quantity", "")
    for it in result["line_items"]:
        if not it.get("name") or it["name"] in ("", "Unknown item"):
            it["name"] = title or "Unknown item"
        if not it.get("quantity"):
            it["quantity"] = tq
    # Flag tenders whose specs are NOT in this PDF but in an external attachment.
    has_specs = any(it.get("specifications") for it in result["line_items"])
    rt = extracted["rich_text"].lower()
    if not has_specs and ("specification document" in rt or "buyer specification" in rt):
        result["specs_status"] = "external_document"
        result["specs_note"] = ("Technical specs are not inline in this PDF — the tender "
                                 "references a separate Buyer Specification Document to download.")
    elif not has_specs:
        result["specs_status"] = "not_found"
    else:
        result["specs_status"] = "inline"
    return result


def structure_tender(extracted: dict) -> dict:
    # Prepend the extracted tables (specs live here) so they're always in view,
    # then as much document text as fits. Guards against the spec table sitting
    # past a naive character cap.
    tables = _all_tables_text(extracted)
    body = f"KEY TABLES:\n{tables}\n\nDOCUMENT TEXT:\n{extracted['rich_text'][:9000]}"
    schema_str = json.dumps(SCHEMA, ensure_ascii=False, indent=2)
    fallback_specs = _specs_from_tables(extracted)

    try:
        from llm import chat
        raw = chat(PROMPT.format(schema=schema_str, body=body), system=SYSTEM, max_tokens=1800)
        parsed = _parse_json(raw)
        result = _coerce(parsed, SCHEMA)
        result["_source"] = "llm"
        # Normalize item key names and backfill specs from the reliable tables.
        items = [_normalize_item(it, fallback_specs) for it in result["line_items"]]
        result["line_items"] = [it for it in items if it]
        if not result["line_items"]:
            result["line_items"] = heuristic_structure(extracted)["line_items"]
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠ LLM structuring failed ({type(e).__name__}: {str(e)[:120]}). Using heuristic fallback.")
        result = heuristic_structure(extracted)
        result["_source"] = "heuristic_fallback"

    return _postprocess(result, extracted)


def main(extracted_json: str | Path | None = None) -> dict:
    extracted_json = Path(extracted_json) if extracted_json else \
        config.OUTPUT_DIR / "fpga_8663118_extracted.json"
    extracted = json.loads(Path(extracted_json).read_text(encoding="utf-8"))

    result = structure_tender(extracted)

    stem = Path(extracted["source_pdf"]).stem
    out = config.OUTPUT_DIR / f"{stem}_structured.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    md = result["metadata"]
    print(f"Structured via {result['_source']}:")
    print(f"  title:    {md['title']}")
    print(f"  org:      {md['issuing_organization']}")
    print(f"  deadline: {md['submission_deadline']}")
    print(f"  items:    {len(result['line_items'])}")
    for it in result["line_items"]:
        print(f"    - {it.get('name')} (qty {it.get('quantity')}, "
              f"{len(it.get('specifications', {}))} specs)")
    if result.get("specs_status") != "inline":
        print(f"  specs:    {result.get('specs_status')} — {result.get('specs_note', 'no specs found in PDF')}")
    print(f"  -> {out.name}")
    return result


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    main(arg)
