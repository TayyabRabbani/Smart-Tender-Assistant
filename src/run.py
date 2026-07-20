"""Step 7 — Orchestrator.

One command to take a tender PDF all the way to a bid/no-bid verdict:

    python src/run.py data/raw/fpga_8663118.pdf      # one tender
    python src/run.py --all                          # every PDF in data/raw

Chains extract -> structure -> match -> pricing in-process, writes each stage's
JSON to data/output/ (plus a consolidated <stem>_final.json), and prints a clean
verdict. Auto-builds the inventory vector DB if it's missing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
import inventory  # noqa: E402
from extract import extract_tender  # noqa: E402
from structure import structure_tender  # noqa: E402
from match import match_tender  # noqa: E402
from pricing import price_tender  # noqa: E402


def _dump(obj: dict, name: str) -> Path:
    p = config.OUTPUT_DIR / name
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _decide(matches: dict, pricing: dict) -> tuple[str, str]:
    rows = matches["matches"]
    matched = [m for m in rows if m["status"] == "matched"]
    if rows and len(matched) == len(rows):
        return "BID", f"all {len(rows)} item(s) matched a compliant product"
    if matched:
        return "PARTIAL_BID", f"{len(matched)}/{len(rows)} item(s) matched"
    # Nothing matched — surface the dominant reason.
    reasons = {m["status"] for m in rows}
    if reasons == {"no_specs_in_tender"}:
        return "NO_BID", "specs not in tender PDF (likely external Buyer Specification Document)"
    return "NO_BID", "no compliant product in inventory for the required specs"


def run_one(pdf_path: str | Path, *, verbose: bool = True) -> dict:
    pdf_path = Path(pdf_path)
    stem = pdf_path.stem

    extracted = extract_tender(pdf_path)
    _dump(extracted, f"{stem}_extracted.json")
    (config.OUTPUT_DIR / f"{stem}_extracted.txt").write_text(
        extracted["rich_text"], encoding="utf-8")

    structured = structure_tender(extracted)
    _dump(structured, f"{stem}_structured.json")

    matches = match_tender(structured)
    _dump(matches, f"{stem}_matches.json")

    pricing = price_tender(structured, matches)
    _dump(pricing, f"{stem}_pricing.json")

    decision, reason = _decide(matches, pricing)

    final = {
        "tender_pdf": pdf_path.name,
        "metadata": structured["metadata"],
        "specs_status": structured.get("specs_status", "inline"),
        "needs_ocr": extracted["needs_ocr"],
        "decision": decision,
        "decision_reason": reason,
        "grand_total_inr": pricing["grand_total"],
        "grand_total_partial": pricing["grand_total_partial"],
        "line_items": [
            {
                "rfp_product": m["rfp_product"],
                "status": m["status"],
                "winner": m["winner"],
                "best_partial": m.get("best_partial"),
            }
            for m in matches["matches"]
        ],
    }
    _dump(final, f"{stem}_final.json")

    if verbose:
        _print_verdict(final)
    return final


def _print_verdict(final: dict) -> None:
    md = final["metadata"]
    icon = {"BID": "✅", "PARTIAL_BID": "🟡", "NO_BID": "⛔"}.get(final["decision"], "•")
    print("\n" + "=" * 64)
    print(f"  {md.get('title')}")
    print(f"  {md.get('issuing_organization')}  |  deadline {md.get('submission_deadline')}")
    if final["needs_ocr"]:
        print("  ⚠ scanned pages detected — OCR fallback not yet wired")
    if final["specs_status"] != "inline":
        print(f"  specs: {final['specs_status']}")
    print("-" * 64)
    for li in final["line_items"]:
        if li["status"] == "matched":
            w = li["winner"]
            print(f"  ✓ {li['rfp_product']}")
            print(f"      -> {w['OEM']} {w['Model']} ({w['compliance']}% compliant)")
        else:
            print(f"  ✗ {li['rfp_product']}  [{li['status']}]")
    total = final["grand_total_inr"]
    tilde = "~" if final["grand_total_partial"] else ""
    print("-" * 64)
    print(f"  {icon} DECISION: {final['decision']} — {final['decision_reason']}")
    print(f"  💰 Total cost: {tilde}INR {total:,}")
    print("=" * 64)


def _ensure_index() -> None:
    if not inventory.INDEX_PATH.exists():
        print("Inventory index missing — building it...")
        inventory.build_index()


def main(argv: list[str]) -> None:
    _ensure_index()

    if argv and argv[0] == "--all":
        pdfs = sorted(config.RAW_DIR.glob("*.pdf"))
        if not pdfs:
            print(f"No PDFs in {config.RAW_DIR}")
            return
        summary = []
        for pdf in pdfs:
            final = run_one(pdf)
            summary.append({"pdf": pdf.name, "decision": final["decision"],
                            "total_inr": final["grand_total_inr"]})
        _dump({"results": summary}, "batch_summary.json")
        print("\n\n#### BATCH SUMMARY ####")
        for s in summary:
            print(f"  {s['decision']:12} INR {s['total_inr']:>10,}  {s['pdf']}")
        return

    if not argv:
        print("Usage: python src/run.py <tender.pdf> | --all")
        return
    run_one(argv[0])


if __name__ == "__main__":
    main(sys.argv[1:])
