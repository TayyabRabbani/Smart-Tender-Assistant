# ⚙️ Smart Tender Assistant

**Scan an RFP → match it against your inventory → build the bill → get a bid / no-bid verdict.**

Smart Tender Assistant reads a procurement tender (a GeM-style PDF), extracts every line item and
its technical specifications, matches each requirement against your product catalogue using
semantic search **re-ranked by a hard spec-compliance check**, computes the cost, and ends
with a clear **BID / PARTIAL_BID / NO_BID** decision — all from one uploaded PDF.

It runs as a CLI or as a small FastAPI web app with a dark, dashboard-style UI.

---

## Why it's different

Semantic similarity alone will happily "match" a product that doesn't actually meet the spec.
Smart Tender Assistant treats tender values as **minimums** (e.g. `256K or higher`, `12 months`) and
will **not** declare a winner unless the candidate genuinely satisfies the mandatory specs —
so a green verdict means the product is really compliant, not just textually similar.

---

## Pipeline

```
PDF ──▶ extract ──▶ structure ──▶ match ──▶ price ──▶ verdict
         (text +      (LLM →       (FAISS +    (cost      (BID /
          tables)      typed        spec        build-up)  PARTIAL /
                       schema)      compliance)             NO_BID)
```

| Stage | Module | What it does |
|-------|--------|--------------|
| 1. Extract   | [`src/extract.py`](src/extract.py)     | PyMuPDF text + pdfplumber tables; strips garbled Hindi/font noise; flags scanned pages that would need OCR. |
| 2. Structure | [`src/structure.py`](src/structure.py) | LLM parses the tender into `metadata`, `eligibility`, and `line_items[]` with per-item specs. Deterministic table-based fallback if the LLM is unavailable. |
| 3. Inventory | [`src/inventory.py`](src/inventory.py) | Embeds the product catalogue once into a FAISS index (the searchable corpus). |
| 4. Match     | [`src/match.py`](src/match.py)         | Semantic retrieval per line item, re-ranked by `spec_compliance()`. |
| 5. Price     | [`src/pricing.py`](src/pricing.py)     | `unit_price × qty + services` per line, plus a grand total. |
| 6. Orchestrate | [`src/run.py`](src/run.py)           | Chains all stages and emits the consolidated verdict. |
| Web API      | [`app.py`](app.py)                     | FastAPI backend + static UI ([`ui/index.html`](ui/index.html)). |

---

## Tech stack

- **Python 3.11**
- **PDF**: `pymupdf`, `pdfplumber` (text-layer extraction, no OCR)
- **LLM + embeddings**: HuggingFace Inference API via `huggingface_hub`
  (default LLM `Qwen/Qwen2.5-Coder-32B-Instruct`, embeddings `sentence-transformers/all-MiniLM-L6-v2`) — no local `torch`
- **Vector store**: `faiss-cpu` + `numpy`
- **Web**: `fastapi`, `uvicorn`, `python-multipart`

---

## Setup

```bash
# 1. Clone
git clone <your-repo-url>
cd smart-tender-assistant

# 2. Create an environment (venv or conda) and install deps
python -m venv .venv
# Windows:  .venv\Scripts\activate     |  macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt

# 3. Add your HuggingFace token
cp .env.example .env
#   then edit .env and set HUGGINGFACEHUB_API_TOKEN
```

Get a token at <https://huggingface.co/settings/tokens> (read access is enough).

---

## Usage

### Web app (recommended)

```bash
python -m uvicorn app:app --reload --port 8000
```

Open <http://127.0.0.1:8000>, drop a tender PDF, and read the verdict. Previously processed
tenders are listed in the sidebar ledger. The inventory vector index builds automatically on
first run.

### Command line

```bash
python src/run.py data/raw/fpga_8663118.pdf   # one tender
python src/run.py --all                        # every PDF in data/raw/
```

Each stage writes its JSON to `data/output/`, ending in a `<name>_final.json` report and a
printed verdict.

---

## Configuring your own catalogue & prices

Everything the engine matches and prices against lives in [`data/inventory/`](data/inventory/):

| File | Purpose |
|------|---------|
| `oem_products.json`  | Your product catalogue — `Product_Type`, `OEM`, `Model`, and a `Specs` map. |
| `product_prices.json`| Per-model unit prices (`default_<Product_Type>` supported as a fallback). |
| `service_prices.json`| Add-on service line items (installation, transport, etc.). |

Which services get added to each line is set by `APPLIED_SERVICES` in [`config.py`](config.py).
After editing `oem_products.json`, delete `data/output/inventory.faiss` (or just restart) to
rebuild the vector index.

---

## Project structure

```
smart-tender-assistant/
├── app.py                 # FastAPI backend + static UI server
├── config.py              # paths, model choices, tuning
├── requirements.txt
├── .env.example           # copy to .env and add your token
├── src/
│   ├── extract.py         # 1. PDF → clean text + tables
│   ├── structure.py       # 2. text → typed tender schema (LLM + fallback)
│   ├── inventory.py       # 3. catalogue → FAISS index
│   ├── embeddings.py      #    shared HF embedding helper
│   ├── llm.py             #    thin HF Inference API wrapper
│   ├── match.py           # 4. requirement → best compliant product
│   ├── pricing.py         # 5. winners → cost build-up
│   └── run.py             # 6. orchestrator (CLI entrypoint)
├── ui/
│   └── index.html         # single-file dashboard UI
└── data/
    ├── inventory/         # your catalogue + price tables  (tracked)
    ├── raw/               # tender PDFs                     (samples tracked)
    └── output/            # generated stage files + index  (gitignored)
```

---

## Notes & current limitations

- **Cost, not a bid price.** The pricing stage computes the *cost* to fulfil the tender, not a
  client-facing quotation/invoice or bid strategy.
- **OCR is detected, not performed.** Scanned pages are flagged; text-layer PDFs work today.
- **External spec documents.** Tenders that reference a separate Buyer Specification Document
  (specs not inline in the PDF) are surfaced as `no_specs_in_tender` rather than guessed.
- **Eligibility** (turnover / EMD / experience) is extracted but does not yet feed the bid
  decision — the verdict is driven purely by spec compliance.
- The HuggingFace Inference API can rate-limit; the structuring stage falls back to a
  deterministic table parser so a throttled endpoint never blocks a run.

---

## License

Add a license of your choice (e.g. MIT) before publishing.
