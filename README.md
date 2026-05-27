# 🌍 Treasury Oracle

**AI Marathon 2026 · Track 3 — Cross-Border Reconciliation**

An autonomous agent that reconciles cross-border payments. It reads payment
proofs (image / PDF, any currency), parses bank statements, performs historical
FX lookups + bank-fee math, and decides each row as **strict / soft /
discrepancy / no_match** — with full per-numeric provenance, a deterministic
verifier pass against the agent's own decisions, and a downloadable audit pack
that traces every figure back to its source.

```
Payment proofs ──► Vision LLM ──► {amount, currency, date, ...} + ocr_quality
Bank statement ──► CSV/XLSX parser ──► transactions + drift/skipped report
                                            │
                  ┌─────────────────────────┘
                  ▼
        Skill-platform agent loop  (LLM tool-calling, MAX_STEPS=6)
            • get_fx_rate (ECB → source-tagged)
            • apply_bank_fee     • fuzzy_compare
            • trace_swift_route  • remember_fact / recall_facts
                  │
                  ▼
        Decision { strict | soft | discrepancy | no_match }
                  │
                  ▼
        FX-trust guardrail  ──► strict on static-fallback → soft
        Verifier pass        ──► overconfident strict → soft (5 rules)
                  │
                  ▼
        Per-numeric provenance attached  ──►  SQLite (per-tenant)
                  │
                  ▼
        PDF Recon Report  +  Audit Defense Pack (with §5.5 provenance table)
        Dunning workflow (LangGraph, durable across restarts)
```

## Highlights

- **Real agentic loop** — `app/agent.py` runs a tool-calling LLM cycle per
  proof, dispatched through a configurable skill registry. Trace persisted
  per-step in SQLite.
- **Per-numeric provenance** — every match carries
  `conversion.provenance.{proof_amount, fx_rate, fee, expected_*, actual_received}`
  with `{value, source, asof, trusted}`. LLM-invented numbers are tagged
  `agent_unverified`. Rendered in the audit-pack PDF §5.5.
- **F2 ensemble verifier** — a deterministic skeptic audits every strict
  claim against 5 overconfidence patterns (diff > 0.5%, date gap, no tool
  calls, no ref overlap, no payer overlap), paired with an LLM auditor that
  can reject the agent's answer and force a retry. Downgrades to soft with
  traceable concerns.
- **Bounded reflection loop** — the agent re-reads its own draft once and
  can call additional skills before committing.
- **Per-tenant agent knobs** — `MAX_STEPS`, match tolerance, reflection
  threshold, model profile, temperature, **and the base system prompt**
  live in `platform_config.agent_knobs`, editable from the Settings UI
  without a redeploy.
- **Per-tenant memory** — a `tenant_notes` table (MEMORY.md pattern)
  auto-injected into the agent's system prompt on every run.
- **Per-tenant banks + FX fallback rates** — no hardcoded constants;
  both seed from the old defaults on first read, then become editable.
- **Continuous regression gate** — labelled fixtures + an adversarial
  hard-set; `python -m app.eval_gate` exits 1 on accuracy drop or any
  hard-fixture regression; `/api/eval/gate` endpoint + UI card.
- **Signed Audit Pack** — Ed25519 manifest on the last page
  (`app/attestation.py`); source-bytes verified against `raw_uploads`
  SHA before issuance. Same keypair signs session tokens.
- **FX trust guardrail** — `get_fx_rate_full` returns `{rate, source, trusted}`;
  the agent refuses strict matches on static-fallback rates.
- **OCR quality gate** — weighted completeness score per proof. Low-quality
  goes straight to human review instead of burning agent tokens.
- **Confidence calibration** — isotonic regression fit on eval runs; raw LLM
  confidence is calibrated before display. Brier-score before/after reported.
- **Eval harness** — labeled fixtures + live-grown fixtures from operator
  soft-match confirmations. Per-class precision/recall/F1, calibration
  buckets, mean tool-calls, token + latency rollups. `temperature=0` by
  default so reruns are reproducible.
- **Reliability** — `with_retry` wrapper + DB-backed circuit breakers
  shared across uvicorn workers via the `breaker_states` table;
  per-tenant rate limits on LLM-spending endpoints; bounded LLM
  concurrency (`AGENT_LLM_CONCURRENCY`, default 4). SQLite per-thread
  connection pool with WAL + `busy_timeout=30s`; typed JSON encoder
  (`safe_dumps`) so Decimals don't silently round-trip as strings.
- **Multi-tenant** — every table scoped by `tenant_id`. The
  `Authorization: Bearer <token>` header is authoritative;
  `x-tenant-id` is back-compat only and never overrides the token.
- **Idempotency** — `/api/reconcile` accepts `Idempotency-Key`; tab-refresh
  returns the cached recon_id instead of re-spending tokens. 409 on key
  reuse with a different body.
- **Column drift detection** — last-seen column mapping persisted per
  `(tenant, bank)`; the UI shows a red/amber banner when bank headers
  change before any reconciliation runs.
- **LangGraph campaign workflow** — durable dunning escalations across days,
  resumable after process restart.

## Stack

- **Backend**: FastAPI · OpenAI SDK (Chutes-compatible) · ReportLab · pandas ·
  scikit-learn (calibration) · LangGraph (durable workflows) · SQLite
- **Frontend**: React + Vite + Tailwind
- **LLM**: vision via `google/gemma-4-31B-turbo-TEE`; reasoning via a Chutes
  **pool** (`GLM-5.1`, `Qwen3.5-397B`, `MiniMax-M2.5` — all TEE variants)
  routed `:latency` so each step picks the lowest-TTFT model. See
  `backend/.env.example`.
- **Auth**: bcrypt passwords + Ed25519-signed session tokens; the same
  keypair signs the Audit Pack manifest, so the trust root is one artifact.
- **FX data**: frankfurter.app (ECB) · exchangerate.host fallback · static
  baked-in fallback (always tagged `trusted=false`)

## Setup

### Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env                   # fill in Chutes keys
python data\generate_samples.py          # sample proofs + statement
uvicorn app.main:app --reload --port 8000
```

PDF uploads also need [poppler](https://github.com/oschwartz10612/poppler-windows/releases) on PATH.

### Frontend

```powershell
cd frontend
npm install
npm run dev
```

Open http://localhost:5173.

### Tests

```powershell
cd backend
.\.venv\Scripts\python -m pytest tests/    # 213/213 green
```

## Demo flow

1. **Upload proofs** — drop the 8 sample proofs from `backend/data/samples/`.
   Watch OCR quality badges (some are intentionally low-completeness).
2. **Upload bank statement** — `bank_statement.csv`. UI shows X-of-Y rows
   parsed, any skipped rows + reasons, and a column-drift banner if headers
   changed since last upload for this bank.
3. **Pick bank** (Maybank by default).
4. **Run Agent** — the trace panel streams: `get_fx_rate(USD→MYR)` →
   `apply_bank_fee` → decision → verifier confirm/downgrade.
5. **Download Reconciliation Report** PDF. For any single match, **Download
   Audit Defense Pack** — see §5.5 Provenance table tracing every number
   to its source (ECB / bank statement / config / SHA-256 of the proof).

Expected outcome: 6 strict matches (diffs in the +8 to −29 bps range — the
sample data is jittered to look like real correspondent-bank flows, not
suspicious 0.00%), 1 soft match awaiting confirmation, 1 flagged
discrepancy, 1 orphan bank transaction.

## API surface (selected)

| Endpoint | What it does |
|---|---|
| `POST /api/extract-proofs` | Vision-LLM OCR with per-proof quality score |
| `POST /api/parse-statement?bank=...` | Parse + skipped rows + column drift report |
| `POST /api/reconcile` | Run agent (or classical); supports `Idempotency-Key` header |
| `GET  /api/session/{recon_id}` | Full session incl. agent trace |
| `GET  /api/report/{recon_id}` | PDF Reconciliation Report |
| `GET  /api/audit-pack/{recon_id}/{match_index}` | Per-match PDF Audit Pack with §5.5 provenance |
| `POST /api/eval/run` | Run eval harness (defaults to `temperature=0`) |
| `GET  /api/eval/runs` · `GET /api/eval/diff/{run_id}` | Eval history + diff vs previous |
| `POST /api/eval/calibrate` | Fit isotonic calibrator from an eval run |
| `GET  /api/platform/skills` · `PUT /api/platform/skills/{id}` | Enable/disable skills, edit per-skill system prompt or model profile |
| `POST /api/platform/wizard` | AI-assisted config from a free-form business profile |
| `GET  /api/memory/{aliases,facts,sessions,uploads,errors,breakers}` | Memory + ops surface |

## Repo map

```
backend/app/
  agent.py              # tool-calling loop + FX guardrail + verifier
  calibration.py        # isotonic confidence calibration
  campaign_workflow.py  # LangGraph durable dunning campaigns
  chutes_client.py      # LLM client with retry + narrow auth fallback
  db.py                 # SQLite + per-thread pool + safe_dumps + drift mappings
  eval.py / eval_api.py # eval harness + diff vs previous run
  matcher.py            # classical reconciliation (fallback / comparison)
  ocr.py                # vision LLM + completeness gate
  parser.py             # statement parser with skipped-row provenance
  platform_config.py    # per-tenant skill + prompt overrides
  platform_api.py       # config CRUD + AI wizard
  reliability.py        # retry policy, circuit breaker, error logging
  skills/               # skill registry — each file registers one tool/capability
  tools.py              # get_fx_rate_full, apply_bank_fee — source-tagged
  uploads.py            # SHA-256 dedup'd raw-upload storage
  audit_pack.py         # per-txn PDF with §5.5 provenance + SHA-256
  report.py             # full-session PDF
```

## Multi-tenant note

Every request honors an `x-tenant-id` header (defaults to `"default"`).
SQLite tables are scoped on `tenant_id`; one tenant can't read another's
sessions, calibrators, prompt overrides, idempotency keys, column mappings,
or live fixtures.

## Team

APU Hackathon team · AI Marathon 2026 · Repo: https://github.com/youamar/treasury-oracle
