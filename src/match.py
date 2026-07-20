"""Step 5 — Matching.

For each tender line item: query the inventory vector DB (semantic retrieval),
then re-rank candidates by a deterministic spec-compliance check. Tender "allowed
values" are treated as minimums (e.g. "256K or higher"), so the winner is the
product that both reads similar AND actually meets the mandatory specs.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from embeddings import embed  # noqa: E402
from inventory import load_index, product_to_text  # noqa: E402


# ---------- spec compliance ----------
def _norm_key(k: str) -> str:
    return re.sub(r"[^a-z0-9]", "", k.lower())


def _num(s: str):
    """First number in s, honoring K/M suffixes. 256K -> 256000."""
    m = re.search(r"([\d.]+)\s*([KkMm]?)", str(s).replace(",", ""))
    if not m:
        return None
    val, suf = float(m.group(1)), m.group(2).upper()
    return val * 1000 if suf == "K" else val * 1_000_000 if suf == "M" else val


def _is_numeric_req(rv: str) -> bool:
    """A requirement is a numeric minimum if it says 'higher/minimum' or is a
    bare number — even with a trailing unit like '(month)'."""
    s = str(rv).lower()
    if re.search(r"\b(higher|more|minimum|min|at ?least)\b", s) or ">=" in s or "≥" in s:
        return True
    s = re.sub(r"\b(or higher|or more|minimum|min|approx|up ?to|\+)\b", "", s)
    s = s.replace(",", "").strip()
    return re.fullmatch(r"[\d.]+\s*[km]?", s) is not None


def spec_compliance(req_specs: dict, prod_specs: dict):
    """Fraction of comparable requirement specs the product satisfies."""
    prod_norm = {_norm_key(k): v for k, v in prod_specs.items()}
    checked = passed = 0
    details = []
    for rk, rv in req_specs.items():
        pk = _norm_key(rk)
        if pk not in prod_norm:
            continue  # product doesn't list this spec — not comparable
        pv = prod_norm[pk]
        if _is_numeric_req(rv):
            rn, pn = _num(rv), _num(pv)
            ok = rn is not None and pn is not None and pn >= rn
        else:
            rtok = set(re.findall(r"[a-z0-9]+", str(rv).lower()))
            ptok = set(re.findall(r"[a-z0-9]+", str(pv).lower()))
            ok = len(rtok & ptok) >= max(1, len(rtok) // 2)
        checked += 1
        passed += int(ok)
        details.append({"spec": rk, "required": rv, "offered": pv, "ok": bool(ok)})
    score = passed / checked if checked else 0.0
    return score, checked, passed, details


# ---------- matching ----------
def _requirement_text(item: dict) -> str:
    specs = ". ".join(f"{k}: {v}" for k, v in item.get("specifications", {}).items())
    return f"{item.get('name', '')}. {specs}"


def match_tender(structured: dict, top_k: int = 5) -> dict:
    index, metas = load_index()
    top_k = min(top_k, len(metas))
    results = []

    for item in structured["line_items"]:
        req_specs = item.get("specifications", {})
        query = _requirement_text(item)
        qvec = embed([query])
        sims, idxs = index.search(qvec, top_k)  # cosine (normalized IP)

        candidates = []
        for sim, i in zip(sims[0], idxs[0]):
            prod = metas[i]
            comp_score, checked, passed, details = spec_compliance(
                req_specs, prod.get("Specs", {}))
            candidates.append({
                "OEM": prod["OEM"],
                "Model": prod["Model"],
                "Product_Type": prod["Product_Type"],
                "semantic_score": round(float(sim) * 100, 1),
                "compliance": round(comp_score * 100, 1),
                "specs_passed": f"{passed}/{checked}",
                "fully_compliant": passed == checked and checked > 0,
                "spec_details": details,
            })
        # Rank: compliance first, then semantic similarity.
        candidates.sort(key=lambda c: (c["compliance"], c["semantic_score"]), reverse=True)
        top = candidates[0] if candidates else None

        # GUARDRAIL: only declare a winner when we could actually verify specs.
        # No specs to compare, or no compliant product -> no winner, flag for review.
        if not req_specs:
            winner, status = None, "no_specs_in_tender"
        elif top is None or not top["fully_compliant"]:
            winner, status = None, "no_compliant_product"
        else:
            winner, status = top, "matched"

        results.append({
            "rfp_product": item.get("name"),
            "status": status,
            "winner": {"OEM": winner["OEM"], "Model": winner["Model"],
                       "compliance": winner["compliance"],
                       "fully_compliant": winner["fully_compliant"]} if winner else None,
            "best_partial": None if winner else (
                {"OEM": top["OEM"], "Model": top["Model"], "compliance": top["compliance"],
                 "specs_passed": top["specs_passed"]} if top else None),
            "candidates": candidates,
        })

    return {"tender": structured["metadata"].get("title"),
            "specs_status": structured.get("specs_status", "inline"),
            "matches": results}


def main(structured_json: str | Path | None = None) -> dict:
    structured_json = Path(structured_json) if structured_json else \
        config.OUTPUT_DIR / "fpga_8663118_structured.json"
    structured = json.loads(Path(structured_json).read_text(encoding="utf-8"))

    result = match_tender(structured)

    stem = Path(structured_json).stem.replace("_structured", "")
    out = config.OUTPUT_DIR / f"{stem}_matches.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    reason = {"no_specs_in_tender": "no specs in tender PDF — cannot assess (specs may be in external doc)",
              "no_compliant_product": "no fully-compliant product in inventory"}
    for m in result["matches"]:
        print(f"\nRequirement: {m['rfp_product']}")
        for c in m["candidates"]:
            mark = "✓" if c["fully_compliant"] else " "
            print(f"  [{mark}] {c['OEM']:16} {c['Model']:20} "
                  f"specs {c['specs_passed']:>4}  sem {c['semantic_score']:>5}")
        if m["status"] == "matched":
            w = m["winner"]
            print(f"  => ✅ WINNER: {w['OEM']} {w['Model']} ({w['compliance']}% compliant)")
        else:
            bp = m.get("best_partial")
            extra = f" (best partial: {bp['OEM']} {bp['Model']} {bp['specs_passed']})" if bp else ""
            print(f"  => ⛔ NO MATCH — {reason.get(m['status'], m['status'])}{extra}")
    print(f"\n  -> {out.name}")
    return result


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    main(arg)
