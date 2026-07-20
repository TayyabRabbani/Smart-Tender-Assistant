"""Central config: paths and model choices for the tender engine."""
import sys as _sys
from pathlib import Path

# Windows consoles default to cp1252 and crash on the ✓/✅/⚠ used in CLI output.
# Force UTF-8 so the scripts print cleanly everywhere. Imported by every module.
for _stream in (_sys.stdout, _sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# --- Paths ---
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RAW_DIR = DATA / "raw"
INVENTORY_DIR = DATA / "inventory"
OUTPUT_DIR = DATA / "output"

OEM_PRODUCTS = INVENTORY_DIR / "oem_products.json"
PRODUCT_PRICES = INVENTORY_DIR / "product_prices.json"
SERVICE_PRICES = INVENTORY_DIR / "service_prices.json"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# --- Extraction tuning ---
# A page with fewer than this many extracted chars is treated as scanned
# (image-only) and routed to the OCR fallback instead of text extraction.
SCANNED_CHARS_PER_PAGE = 80

# Devanagari Unicode block (Hindi). Stripped during cleaning because every
# value we need on GeM bids is in English and the Hindi glyphs are font-garbled.
DEVANAGARI_RANGE = ("ऀ", "ॿ")

# --- LLM backend (chosen: HuggingFace endpoint) ---
LLM_BACKEND = "huggingface"
HF_REPO_ID = "Qwen/Qwen2.5-Coder-32B-Instruct"
HF_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# --- Pricing ---
# Services added to each line item's cost. Edit to match what a tender requires.
APPLIED_SERVICES = ["Installation & Commissioning", "Transport & Handling"]
