"""Step 6 — Pricing (cost build-up only, no bid strategy).

For each winning product from the match step:
    product_cost = unit_price x quantity
    services_cost = sum of applied service prices (config.APPLIED_SERVICES)
    line_total = product_cost + services_cost
Grand total = sum of line totals. This is the computed COST, not a bid price.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _to_int_qty(q) -> int:
    try:
        return int(str(q).strip().split()[0])
    except (ValueError, IndexError):
        return 1


def _unit_price(model: str, product_type: str, prices: dict):
    """Look up by Model, then default_<Product_Type>; else None (to be quoted)."""
    if model in prices:
        return prices[model], "model"
    key = f"default_{product_type}"
    if key in prices:
        return prices[key], "default_type"
    return None, "to_be_quoted"


def _services_cost(prices: dict):
    applied, missing, total = [], [], 0
    for name in config.APPLIED_SERVICES:
        if name in prices:
            applied.append({"name": name, "price": prices[name]})
            total += prices[name]
        else:
            missing.append(name)
    return applied, missing, total


def price_tender(structured: dict, matches: dict) -> dict:
    product_prices = _load(config.PRODUCT_PRICES)
    service_prices = _load(config.SERVICE_PRICES)

    line_items = structured["line_items"]
    match_rows = matches["matches"]

    priced, grand_total, any_tbq = [], 0, False
    for item, m in zip(line_items, match_rows):
        winner = m.get("winner")
        qty = _to_int_qty(item.get("quantity"))

        if not winner:
            status_msg = {
                "no_specs_in_tender": "Not priced — no specs in tender PDF (likely external spec document)",
                "no_compliant_product": "Not priced — no fully-compliant product in inventory",
            }.get(m.get("status"), "Not priced — no compliant match")
            priced.append({"rfp_product": m.get("rfp_product"),
                           "status": status_msg, "line_total": None})
            any_tbq = True
            continue

        unit_price, source = _unit_price(
            winner["Model"], winner.get("Product_Type", ""), product_prices)
        services, missing_services, services_cost = _services_cost(service_prices)

        if unit_price is None:
            product_cost = None
            line_total = None
            any_tbq = True
        else:
            product_cost = unit_price * qty
            line_total = product_cost + services_cost
            grand_total += line_total

        priced.append({
            "rfp_product": m.get("rfp_product"),
            "winning_oem": winner["OEM"],
            "winning_model": winner["Model"],
            "fully_compliant": winner.get("fully_compliant"),
            "unit_price": unit_price,
            "price_source": source,
            "quantity": qty,
            "product_cost": product_cost,
            "services": services,
            "services_cost": services_cost,
            "missing_services": missing_services,
            "line_total": line_total,
        })

    return {
        "tender": structured["metadata"].get("title"),
        "currency": "INR",
        "line_items": priced,
        "grand_total": grand_total,
        "grand_total_partial": any_tbq,  # True if some item couldn't be fully priced
    }


def _inr(n) -> str:
    return f"INR {n:,}" if isinstance(n, int) else "To Be Quoted"


def main(structured_json: str | Path | None = None,
         matches_json: str | Path | None = None) -> dict:
    structured_json = Path(structured_json) if structured_json else \
        config.OUTPUT_DIR / "fpga_8663118_structured.json"
    matches_json = Path(matches_json) if matches_json else \
        config.OUTPUT_DIR / "fpga_8663118_matches.json"

    structured = _load(Path(structured_json))
    matches = _load(Path(matches_json))
    result = price_tender(structured, matches)

    stem = Path(structured_json).stem.replace("_structured", "")
    out = config.OUTPUT_DIR / f"{stem}_pricing.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Pricing — {result['tender']}  ({result['currency']})\n")
    for li in result["line_items"]:
        if li.get("line_total") is None and "winning_model" not in li:
            print(f"  {li['rfp_product']}: {li.get('status')}")
            continue
        print(f"  {li['winning_oem']} {li['winning_model']}  ({li['price_source']})")
        print(f"    unit {_inr(li['unit_price'])} x {li['quantity']} = {_inr(li['product_cost'])}")
        for s in li["services"]:
            print(f"    + {s['name']}: {_inr(s['price'])}")
        print(f"    line total: {_inr(li['line_total'])}")
    bar = "~" if result["grand_total_partial"] else "="
    print(f"\n  GRAND TOTAL {bar} {_inr(result['grand_total'])}"
          + ("  (partial — some items to be quoted)" if result["grand_total_partial"] else ""))
    print(f"\n  -> {out.name}")
    return result


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else None
    b = sys.argv[2] if len(sys.argv) > 2 else None
    main(a, b)
