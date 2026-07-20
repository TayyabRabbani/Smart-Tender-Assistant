"""Thin, swappable LLM wrapper around the HuggingFace Inference API.

Kept deliberately small so the backend can be swapped later (config.LLM_BACKEND).
Loads the token from .env and retries with backoff on transient HF errors.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

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


def chat(prompt: str, *, system: str | None = None,
         max_tokens: int = 1800, temperature: float = 0.0,
         retries: int = 3) -> str:
    """Single-turn completion. Returns the assistant text, or raises on failure."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = _get_client().chat_completion(
                messages=messages,
                model=config.HF_REPO_ID,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return resp.choices[0].message.content
        except Exception as e:  # noqa: BLE001 — HF raises many transient types
            last_err = e
            wait = 2 * attempt
            print(f"  LLM attempt {attempt}/{retries} failed ({type(e).__name__}); "
                  f"retrying in {wait}s...")
            time.sleep(wait)
    raise RuntimeError(f"LLM call failed after {retries} attempts: {last_err}")
