"""Step 4 — Inventory vector store.

Embed the org's product catalogue ONCE into a FAISS index. This is the corpus;
tender requirements are queried against it (see match.py). Rebuild only when the
catalogue changes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import faiss
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from embeddings import embed  # noqa: E402

INDEX_PATH = config.OUTPUT_DIR / "inventory.faiss"
META_PATH = config.OUTPUT_DIR / "inventory_meta.json"


def product_to_text(prod: dict) -> str:
    """One searchable description per product (type, OEM, model, specs)."""
    specs = ". ".join(f"{k}: {v}" for k, v in prod.get("Specs", {}).items())
    return (f"{prod.get('Product_Type', '')} by {prod.get('OEM', '')}, "
            f"model {prod.get('Model', '')}. {specs}")


def build_index() -> faiss.Index:
    products = json.loads(config.OEM_PRODUCTS.read_text(encoding="utf-8"))
    if isinstance(products, dict) and "products" in products:
        products = products["products"]

    texts = [product_to_text(p) for p in products]
    vectors = embed(texts)  # (n, dim), normalized

    index = faiss.IndexFlatIP(vectors.shape[1])  # inner product on normalized == cosine
    index.add(vectors)

    faiss.write_index(index, str(INDEX_PATH))
    META_PATH.write_text(json.dumps(products, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Built inventory index: {len(products)} products, dim {vectors.shape[1]}.")
    print(f"  -> {INDEX_PATH.name}")
    print(f"  -> {META_PATH.name}")
    return index


def load_index() -> tuple[faiss.Index, list[dict]]:
    if not INDEX_PATH.exists():
        raise FileNotFoundError("Inventory index missing — run inventory.py first.")
    index = faiss.read_index(str(INDEX_PATH))
    metas = json.loads(META_PATH.read_text(encoding="utf-8"))
    return index, metas


if __name__ == "__main__":
    build_index()
