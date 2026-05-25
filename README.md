# 🌍 Global Treasury Agent

**AI Marathon 2026 · Track 3 — Cross-Border Reconciliation**

An autonomous agent that ingests payment proofs (images / PDFs in any currency),
parses local bank statements, and reconciles them by performing historical FX
lookups + bank-fee calculations. Outputs a downloadable PDF reconciliation
report and a discrepancy summary for unmatched items.

## Architecture

```
Payment proofs ──► Vision LLM (Chutes Gemma-4-31B) ──► structured JSON
Bank statement ──► CSV/XLSX parser              ──► transactions
                                                  │
              ┌───────────────────────────────────┘
              ▼
   Matcher Agent (tool-calling LLM)
     • get_fx_rate(from, to, date)   → frankfurter.app / static fallback
     • apply_bank_fee(amount, bank)  → config-driven
     • match within 2% tolerance + 5-day window
              │
   ┌──────────┴──────────┐
   ▼                     ▼
 PDF Report          Discrepancy Summary
```

## Stack

- **Backend**: FastAPI · OpenAI SDK (Chutes-compatible) · ReportLab · pandas
- **Frontend**: React + Vite + Tailwind
- **LLM**: `google/gemma-4-31B-turbo-TEE` on [Chutes.ai](https://chutes.ai)
- **FX data**: frankfurter.app (free, ECB); static fallback rates baked in

## Setup

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate     # Windows
pip install -r requirements.txt
copy .env.example .env     # API keys already filled in
python data\generate_samples.py    # creates sample proofs + statement
uvicorn app.main:app --reload --port 8000
```

PDF uploads also need [poppler](https://github.com/oschwartz10612/poppler-windows/releases) on PATH.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

## Demo flow

1. Upload the 7 sample proofs from `backend/data/samples/`.
2. Upload `backend/data/samples/bank_statement.csv`.
3. Pick a bank (Maybank by default).
4. Click **Run Agent** — watch the matches roll in.
5. Download the PDF Reconciliation Report.

Expected outcome: 6 clean matches across USD/EUR/SGD/GBP/JPY/CNY, 1 flagged
discrepancy (proof_07), and 1 orphan bank transaction.

## Agentic features (judge-aligned)

| Rubric item | Where in code |
|---|---|
| Multimodal OCR | `ocr.py` — Chutes vision LLM, structured JSON output |
| Tool-based reasoning | `tools.py` — `get_fx_rate` (frankfurter.app), `apply_bank_fee` |
| Two-tier agentic matching | `matcher.py` — strict + fuzzy "uncle's account" tier |
| Soft-match learning | `fuzzy.py` — rapidfuzz + in-memory alias memory |
| **SWIFT route time-machine** | `swift.py` — infers correspondent-bank chain from currency + BIC patterns when a fee gap is detected |
| **Auto-dunning (multilingual)** | `dunning.py` — LLM drafts collection emails in payer's language |
| **"Blame the Fed" boss chart** | `dunning.py:boss_chart` — FX-movement explainer with LLM-written narrative |
| Voice ingestion | `voice.py` — transcript → LLM extraction (Whisper optional) |
| Mock live inbox | `main.py:/api/inbox/poll` — watches `data/inbox/` like a WhatsApp feed |
| Meaningful outputs | `report.py` — PDF Reconciliation Report + Discrepancy Summary |
| **Retroactive FX peak lock** | `fx_history.py` — 30d series, click-to-lock simulator, missed-profit vs avg |
| **FX Jackpot watcher** | `fx_history.py:watcher_check` + `FXWatcher.jsx` — threshold alerts with audio |
| **Boss documentary mode** | `documentary.py` — LLM writes 3-para macro narrative + browser TTS voice-over |
| **Sales submission validator** | `validator.py` — sassy LLM critique gate for sloppy proofs |
| **Audit Defense Pack PDF** | `audit_pack.py` — per-txn chain-of-custody evidence bundle for LHDN |
| **One-click month-end close** | `/api/month-end-close` — batch-ingests inbox, parses, ready to reconcile |
| **Dunning escalation campaign** | `campaign.py` — 4-stage cadence (Day 1/3/7/14), multilingual, state-tracked |

## Team

APU Hackathon team · AI Marathon 2026
