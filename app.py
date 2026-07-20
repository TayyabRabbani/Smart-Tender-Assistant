"""FastAPI backend for the tender engine.

Wraps the pipeline (run_one) behind HTTP so a browser can:
  - upload a tender PDF and get the bid/no-bid verdict
  - browse previously processed tenders (the ledger)

Run:  D:\\conda_envs\\EY\\python.exe -m uvicorn app:app --reload --port 8000
Then open http://127.0.0.1:8000
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
import inventory  # noqa: E402
from run import run_one  # noqa: E402

from fastapi import FastAPI, File, HTTPException, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402

app = FastAPI(title="Tender Engine")

# Allow the UI to call the API even when opened from a file:// / preview origin.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

UI_DIR = ROOT / "ui"


@app.on_event("startup")
def _startup() -> None:
    # Build the inventory vector DB once if it isn't there (or was wiped).
    if not inventory.INDEX_PATH.exists():
        inventory.build_index()


def _safe_stem(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("_")
    return stem or "tender"


def _load(name: str) -> dict:
    p = config.OUTPUT_DIR / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _report(stem: str) -> dict:
    """Assemble the full report a tender's UI view needs from the stage files."""
    final = _load(f"{stem}_final.json")
    if not final:
        raise HTTPException(404, f"No processed tender named '{stem}'")
    final["pricing"] = _load(f"{stem}_pricing.json")
    final["match_detail"] = _load(f"{stem}_matches.json")
    final["stem"] = stem
    return final


@app.get("/")
def index():
    return FileResponse(UI_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "index_built": inventory.INDEX_PATH.exists()}


@app.post("/api/process")
async def process(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")
    stem = _safe_stem(file.filename)
    dest = config.RAW_DIR / f"{stem}.pdf"
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(await file.read())

    try:
        run_one(dest, verbose=False)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Processing failed: {type(e).__name__}: {e}")

    return JSONResponse(_report(stem))


@app.get("/api/tenders")
def tenders():
    """Ledger: every processed tender, newest first."""
    rows = []
    for p in config.OUTPUT_DIR.glob("*_final.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        rows.append({
            "stem": p.name.replace("_final.json", ""),
            "title": d.get("metadata", {}).get("title"),
            "deadline": d.get("metadata", {}).get("submission_deadline"),
            "decision": d.get("decision"),
            "grand_total_inr": d.get("grand_total_inr"),
            "mtime": p.stat().st_mtime,
        })
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return {"tenders": rows}


@app.get("/api/tenders/{stem}")
def tender(stem: str):
    return _report(_safe_stem(stem))
