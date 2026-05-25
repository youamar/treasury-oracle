# Project Status — pick up here next session

Last updated: 2026-05-25 (end of Wave 1)

## TL;DR
Treasury Oracle — AI Marathon 2026 Track 3 (cross-border reconciliation).
**Real agentic LLM tool-calling loop + SQLite persistence are done and verified live.**
68/68 tests pass. Live smoke test against Chutes Gemma-4-31B passes.

Repo: https://github.com/youamar/treasury-oracle

## How to resume

```powershell
cd D:\APUHackathon\backend
.\.venv\Scripts\activate   # already created
python -m pytest tests/    # should be 68/68 green
$env:PYTHONIOENCODING="utf-8"
python smoke_test_agent.py # live Chutes agent run, ~60s
```

Frontend:
```powershell
cd D:\APUHackathon\frontend
npm run dev
```

API: http://localhost:8000  ·  UI: http://localhost:5173

## What's built (15 features, all wired end-to-end)
- Vision LLM OCR (Chutes Gemma-4-31B)
- **Real agentic loop** (`app/agent.py`) — function-calling per proof, trace persisted
- **SQLite** (`app/db.py`) — sessions, matches, soft_matches, discrepancies, campaigns, watchers, payer_aliases, agent_trace
- Strict + fuzzy/soft matching with payer-alias learning
- SWIFT correspondent-bank route inference + animated visualizer
- Retroactive FX peak analyzer + jackpot watcher with audio
- Multilingual auto-dunning + 4-stage escalation campaigns
- "Blame the Fed" boss chart + documentary mode with TTS
- Audit Defense Pack PDF
- Mock live inbox + voice note ingestion
- Sales submission validator + month-end close
- Reconciliation Report PDF

## What's NOT done — pick from here

### Code work — Waves 2 & 3 from the review
**Wave 2 (~1hr) — reliability**
- [ ] S2: async OCR via `AsyncOpenAI` + `asyncio.gather` (biggest demo-quality win — currently 8 proofs = ~40s UI freeze)
- [ ] R1: `timeout=30` on every Chutes call
- [ ] R2: tenacity retry with backoff on 429/5xx
- [ ] R3: file size limit (10MB) + MIME validation on /api/extract-proofs
- [ ] R6: fix `chutes_client.chat()` fallback — only swap key on auth/rate errors, not arbitrary exceptions
- [ ] C11: session TTL cleanup job (low priority — SQLite handles growth fine)

**Wave 3 (~1hr) — correctness polish**
- [ ] C2: month-end close end-to-end (currently ingests inbox, then asks for statement upload)
- [ ] C5: jitter sample data so matches don't show 0.00% (looks too rigged)
- [ ] C6: SHA-256 of source bytes in audit pack
- [ ] C9: parser column matching — "AMOUNT (CREDIT)" alias edge case
- [ ] L1: `datetime.utcnow()` → `datetime.now(UTC)` in `report.py`, `audit_pack.py`

### Hackathon deliverables — submission deadline 2026-05-26
- [ ] **Pitch deck** (≤10 slides) — outline ready in my head, can draft fast
- [ ] **Agent Framework Diagram** (inside deck)
- [ ] **3–4 min Video Demo** — script + record
- [ ] Repo README is solid; verify clone-and-run works on a fresh machine

## Architecture summary (for the deck)

```
            ┌──────────────────────────────────────┐
Payment    │  Vision LLM (Chutes Gemma-4-31B)     │
proof  ──► │  → structured JSON                   │
PNG/PDF    └──────────────────────────────────────┘
                          │
                          ▼
            ┌──────────────────────────────────────┐
            │  ReconciliationAgent (per proof)     │
            │  ┌────────────────────────────────┐  │
            │  │ LLM tool-call loop ≤ MAX_STEPS │  │
            │  │   • get_fx_rate(...)           │  │
            │  │   • apply_bank_fee(...)        │  │
            │  │   • fuzzy_compare(...)         │  │
            │  │   • trace_swift_route(...)     │  │
            │  └────────────────────────────────┘  │
            │  → final JSON decision               │
            │  → every step → agent_trace table    │
            └──────────────────────────────────────┘
                          │
       ┌──────────────────┼──────────────────┐
       ▼                  ▼                  ▼
   strict match     soft match         discrepancy
       │              (needs                 │
       │           confirmation)             ▼
       │                                SWIFT route
       │                                inference
       ▼
   ┌─────────────────────────────────────────────┐
   │  SQLite (treasury.db)                       │
   │  sessions · matches · soft_matches ·        │
   │  discrepancies · campaigns · watchers ·     │
   │  payer_aliases · agent_trace                │
   └─────────────────────────────────────────────┘
       │
       ▼
   PDF Recon Report  +  Per-txn Audit Defense Pack
   Dunning Campaign  +  Boss Documentary
```

## Known limitations / honest gaps
1. Sync OCR blocks event loop (Wave 2)
2. No timeouts on LLM calls (Wave 2)
3. Static fallback FX rates are stale guesses (cosmetic for demo, dangerous in prod)
4. Month-end close button doesn't fully automate (Wave 3)
5. Tests mock the LLM — real prompt drift would slip through (covered partly by smoke tests)
6. CORS wide open (`allow_origins=["*"]`) — fine for hackathon

## Privacy reminder
This Claude conversation is on JQ Bung's account. If you continue here, he can read it.
Switch to your own Claude account next session if that matters.
Git is now using `amarcky18@gmail.com` for this repo locally.

## Account / API
- GitHub: `youamar` ← active
- Chutes API keys in `backend/.env.example` (currently public on the repo)
- Pro account pending; switch model env vars when received

## Suggested next session order
1. **Decision first**: more code (Wave 2) or pivot to deck/video?
2. If video — Wave 2's async OCR matters most (kills UI freeze during recording)
3. Pitch deck draft can be done in parallel by anyone non-coding on the team
