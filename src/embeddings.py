"""Embeddings via the HuggingFace Inference API (no local torch).

One place so the inventory builder and the matcher embed text the same way.
Vectors are L2-normalized so a FAISS inner-product index == cosine similarity.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

load_dotenv(config.ROOT / ".env")

_client = None


def _get_client():
    global _client
    if _client is None:
        from huggingface_hub import InferenceClient
        token = os.getenv("HUGGINGFACEHUB_API_TOKEN")
        if not token:
            raise RuntimeError("HUGGINGFACEHUB_API_TOKEN not set in .env")
        _client = InferenceClient(token=token)
    return _client


def _embed_one(text: str, retries: int = 3) -> np.ndarray:
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            vec = np.asarray(
                _get_client().feature_extraction(text, model=config.HF_EMBED_MODEL),
                dtype="float32",
            )
            return vec.reshape(-1)  # some models return (1, dim)
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.5 * attempt)
    raise RuntimeError(f"Embedding failed after {retries} attempts: {last_err}")


def embed(texts: list[str]) -> np.ndarray:
    """Return an (n, dim) float32 matrix of L2-normalized embeddings."""
    vecs = np.vstack([_embed_one(t) for t in texts])
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vecs / norms
