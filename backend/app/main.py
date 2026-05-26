"""FastAPI entrypoint — Global Treasury Agent."""
import os
import uuid
from pathlib import Path
import hashlib
import time
from contextlib import asynccontextmanager
from collections import deque
from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from .ocr import extract_payment_proof, pdf_to_images, extract_payment_proofs_batch
from .parser import parse_bank_statement, parse_bank_statement_detailed
from .matcher import reconcile as reconcile_classical
from .agent import reconcile_agent
from .report import build_report_pdf
from .dunning import generate_dunning, boss_chart
from .voice import transcribe_audio, extract_from_transcript
from .fuzzy import remember_alias
from .fx_history import peak_analysis, what_if, watcher_check, get_fx_series
from .validator import validate_submission
from .audit_pack import build_audit_pack
from .campaign import create_campaign, advance_campaign, mark_paid, list_campaigns
from . import campaign_workflow as cwf
from .documentary import documentary_narrative
from . import db
from . import skills as _skills  # noqa: F401 — registers all skills on import
from .platform_api import router as platform_router
from .memory_api import router as memory_router
from .eval_api import router as eval_router
from . import uploads as _uploads

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    db.init_db()
    yield
    # Shutdown
    db.reset_pool()


app = FastAPI(title="Global Treasury Agent", version="0.4.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# ---------- per-tenant rate limiting (R-5) ----------
# Sliding-window in-memory limits per (tenant, endpoint). Defaults are
# generous for the hackathon demo but block a runaway client from draining
# Chutes quota. Returns 429 with Retry-After when exceeded.

_RATE_LIMITS = {
    # endpoint suffix → (max_requests, window_seconds)
    "POST /api/reconcile":           (30, 60),
    "POST /api/eval/run":            (10, 60),
    "POST /api/eval/gate":           (5,  60),
    "POST /api/platform/wizard":     (10, 60),
    "POST /api/extract-proofs":      (60, 60),
}
_rate_windows: dict[tuple[str, str], deque] = {}


@app.middleware("http")
async def request_scope_mw(request: Request, call_next):
    # Tenant context for this request.
    tenant = request.headers.get("x-tenant-id") or "default"
    token = db._current_tenant.set(tenant)

    # Rate limit check (only on configured endpoints).
    key_endpoint = f"{request.method} {request.url.path}"
    limit_cfg = _RATE_LIMITS.get(key_endpoint)
    if limit_cfg is not None:
        max_req, window = limit_cfg
        window_key = (tenant, key_endpoint)
        now = time.monotonic()
        q = _rate_windows.setdefault(window_key, deque())
        # Drop entries outside the window.
        while q and (now - q[0]) > window:
            q.popleft()
        if len(q) >= max_req:
            db._current_tenant.reset(token)
            from fastapi.responses import JSONResponse
            retry_after = max(1, int(window - (now - q[0])))
            return JSONResponse(
                status_code=429,
                content={
                    "detail": f"rate limit exceeded: {max_req} requests per {window}s",
                    "retry_after_seconds": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )
        q.append(now)

    try:
        return await call_next(request)
    finally:
        db._current_tenant.reset(token)

app.include_router(platform_router)
app.include_router(memory_router)
app.include_router(eval_router)

INBOX_DIR = Path(__file__).resolve().parent.parent / "data" / "inbox"
INBOX_DIR.mkdir(parents=True, exist_ok=True)
INBOX_SEEN: set[str] = set()


def _safe_inbox_path(filename: str) -> Path:
    """Reject path traversal."""
    name = Path(filename).name
    if not name or name != filename:
        raise HTTPException(400, "invalid filename")
    return INBOX_DIR / name


@app.get("/health")
def health(): return {"status": "ok"}


# ---------- core pipeline ----------

@app.post("/api/extract-proofs")
async def extract_proofs(files: list[UploadFile] = File(...)):
    # Read all into memory once, then guardrail-check before processing.
    blobs: list[tuple[str, bytes]] = []
    for f in files:
        data = await f.read()
        name = f.filename or "upload"
        try:
            _uploads.validate_file(name, len(data), _uploads.ALLOWED_PROOF_EXTS)
        except _uploads.UploadRejected as e:
            raise HTTPException(e.status_code, e.reason)
        blobs.append((name, data))
    try:
        _uploads.validate_batch([len(b) for _, b in blobs])
    except _uploads.UploadRejected as e:
        raise HTTPException(e.status_code, e.reason)

    # Flatten everything to a list of (image_bytes, source_name, sha256) so
    # we can OCR them all concurrently regardless of whether they came from
    # standalone images or PDF page extraction.
    ocr_items: list[tuple[bytes, str]] = []
    sha_by_index: list[str | None] = []
    errors: list[dict] = []
    for name, data in blobs:
        try:
            rec = _uploads.store_bytes(name, data, purpose="proof")
            sha = rec.get("sha256")
            if name.lower().endswith(".pdf"):
                images = pdf_to_images(data)
                for i, img in enumerate(images):
                    ocr_items.append((img, f"{name}#p{i+1}"))
                    sha_by_index.append(sha)
            else:
                ocr_items.append((data, name))
                sha_by_index.append(sha)
        except Exception as e:
            errors.append({"source_file": name, "error": str(e)})

    proofs = await extract_payment_proofs_batch(ocr_items) if ocr_items else []
    for p, sha in zip(proofs, sha_by_index):
        p["source_sha256"] = sha
    return {"proofs": proofs + errors}


@app.post("/api/parse-statement")
async def parse_statement(file: UploadFile = File(...),
                          bank: str = Query("default")):
    data = await file.read()
    name = file.filename or "statement.csv"
    try:
        _uploads.validate_file(name, len(data), _uploads.ALLOWED_STATEMENT_EXTS)
    except _uploads.UploadRejected as e:
        raise HTTPException(e.status_code, e.reason)
    try:
        rec = _uploads.store_bytes(name, data, purpose="statement")
        parsed = parse_bank_statement_detailed(data, name)
    except Exception as e:
        from . import reliability as _rel
        _rel.record_error("parse-statement", e, context={"filename": name})
        raise HTTPException(400, str(e))

    # F5: column drift — compare against the last successful parse for this
    # (tenant, bank). Surface a warning before the operator hits Reconcile.
    previous = db.get_column_mapping(bank)
    drift = db.compute_column_drift(previous, parsed["headers"],
                                    parsed["columns_detected"])
    # Remember the current mapping for next time, unless drift is severe and
    # we'd rather block the silent overwrite.
    if drift["severity"] != "fields_moved":
        db.remember_column_mapping(bank, parsed["headers"],
                                   parsed["columns_detected"])

    return {
        "transactions": parsed["transactions"],
        "skipped": parsed["skipped"],
        "columns_detected": parsed["columns_detected"],
        "headers": parsed["headers"],
        "warnings": parsed["warnings"],
        "row_count": parsed["row_count"],
        "inbound_count": parsed["inbound_count"],
        "outbound_count": parsed["outbound_count"],
        "source_sha256": rec.get("sha256"),
        "column_drift": drift,
    }


class ReconcileRequest(BaseModel):
    proofs: list[dict]
    transactions: list[dict]
    bank: str = "default"
    mode: str = "agent"  # "agent" | "classical"
    # Client-supplied so the UI can poll /api/session/{id} for live trace
    # rendering while the agent is still mid-flight.
    session_id: str | None = None


def _request_hash(body: "ReconcileRequest") -> str:
    """Stable hash of the meaningful inputs. Order-independent over txns/proofs
    so re-uploading the same files in a different order is still a cache hit."""
    canonical = {
        "bank": body.bank,
        "mode": body.mode,
        "proofs": sorted(db.safe_dumps(p, sort_keys=True) for p in body.proofs),
        "transactions": sorted(
            db.safe_dumps(t, sort_keys=True) for t in body.transactions
        ),
    }
    return hashlib.sha256(
        db.safe_dumps(canonical, sort_keys=True).encode("utf-8")
    ).hexdigest()


@app.post("/api/reconcile")
async def reconcile_endpoint(
    body: ReconcileRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    # Idempotency: if the client sent a key and we've seen it before with the
    # same body, return the cached session — no LLM tokens spent on a refresh.
    req_hash = _request_hash(body) if idempotency_key else None
    if idempotency_key:
        try:
            existing_recon_id = db.lookup_idempotency(idempotency_key, req_hash)
        except db.IdempotencyConflict as e:
            raise HTTPException(409, str(e))
        if existing_recon_id:
            cached = db.load_session(existing_recon_id)
            if cached:
                return {**cached, "idempotent_replay": True}

    if body.mode == "classical":
        result = reconcile_classical(body.proofs, body.transactions, body.bank)
        recon_id = body.session_id or str(uuid.uuid4())[:8]
        result["mode"] = "classical"
        result["recon_id"] = recon_id
        db.save_session(recon_id, body.bank, result)
    else:
        result = reconcile_agent(body.proofs, body.transactions, body.bank,
                                 session_id=body.session_id)
        recon_id = result.get("recon_id")

    if idempotency_key and recon_id:
        db.store_idempotency(idempotency_key, req_hash, recon_id)
    return result


@app.get("/api/session/{recon_id}")
def get_session(recon_id: str):
    s = db.load_session(recon_id)
    if not s:
        raise HTTPException(404, "session not found")
    return s


@app.get("/api/session/{recon_id}/trace")
def get_session_trace(recon_id: str):
    """Live trace events for an in-flight or completed session. Returns 200
    with an empty list even if the session hasn't been finalized yet, so the
    UI can poll while the agent is still running."""
    return {"trace": db.get_trace(recon_id)}


@app.get("/api/session/{recon_id}/narrative")
def get_session_narrative(recon_id: str, refresh: bool = False):
    """Plain-English summary of the reconciliation for an exec / auditor.
    Generated lazily and cached per session — set ?refresh=true to regenerate."""
    from . import narrative as _narr
    out = _narr.get_or_create(recon_id, force=refresh)
    if out is None:
        raise HTTPException(404, "session not found")
    return out


@app.get("/api/report/{recon_id}")
def get_report(recon_id: str):
    s = db.load_session(recon_id)
    if not s:
        raise HTTPException(404, "session not found")
    pdf = build_report_pdf(s, s["bank"])
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="reconciliation_{recon_id}.pdf"'},
    )


# ---------- soft-match confirmation ----------

class ConfirmSoftMatch(BaseModel):
    canonical_payer: str
    observed_in_txn: str
    soft_match_id: int | None = None  # if provided, promote to strict match in DB


def _capture_live_fixture_from_soft(soft_match_id: int, verdict: str):
    """Find the soft match by id, snapshot it as a live fixture with the
    operator's verdict so future eval runs include real-world labels."""
    with db.conn() as c:
        row = c.execute(
            "SELECT sm.*, s.bank FROM soft_matches sm "
            "JOIN sessions s ON s.id = sm.session_id "
            "WHERE sm.id = ? AND sm.tenant_id = ?",
            (soft_match_id, db.current_tenant()),
        ).fetchone()
    if not row:
        return
    import json as _json
    proof = _json.loads(row["proof_json"])
    txn = _json.loads(row["txn_json"])
    expected_decision = "strict" if verdict == "confirm" else "no_match"
    db.add_live_fixture(
        fixture_id=f"live-{row['session_id']}-{soft_match_id}-{verdict}",
        bank=row["bank"],
        proof=proof,
        txn_candidates=[txn],
        expected_decision=expected_decision,
        expected_txn_id=txn.get("id") if verdict == "confirm" else None,
        source=f"soft_match_{verdict}",
        session_id=row["session_id"],
        notes=f"operator-{verdict}ed soft match {soft_match_id}",
    )


@app.post("/api/soft-match/confirm")
def confirm_soft(body: ConfirmSoftMatch):
    """User confirmed a soft match — remember the alias, promote to strict
    match, and snapshot the case as a live fixture for the eval pack."""
    remember_alias(body.canonical_payer, body.observed_in_txn)
    promoted = None
    if body.soft_match_id is not None:
        promoted = db.promote_soft_match(body.soft_match_id)
        try:
            _capture_live_fixture_from_soft(body.soft_match_id, "confirm")
        except Exception:
            pass  # fixture capture is observational
    return {
        "ok": True,
        "remembered": [body.canonical_payer, body.observed_in_txn],
        "session": promoted,
    }


class RejectSoftMatch(BaseModel):
    soft_match_id: int


@app.post("/api/soft-match/reject")
def reject_soft(body: RejectSoftMatch):
    """Operator rejected a soft match — snapshot as a no_match live fixture."""
    try:
        _capture_live_fixture_from_soft(body.soft_match_id, "reject")
    except Exception:
        pass
    return {"ok": True}


# ---------- dunning email ----------

class DunningRequest(BaseModel):
    client_name: str
    invoice_ref: str
    invoice_amount: float
    invoice_ccy: str
    received_local: float
    local_ccy: str
    shortfall_invoice: float


@app.post("/api/dunning")
def dunning(body: DunningRequest):
    return generate_dunning(**body.model_dump())


# ---------- boss chart ----------

class BossChartRequest(BaseModel):
    amount: float
    from_ccy: str
    to_ccy: str
    invoice_date: str
    payment_date: str
    actual_local: float


@app.post("/api/boss-chart")
def boss(body: BossChartRequest):
    return boss_chart(**body.model_dump())


# ---------- voice ----------

@app.post("/api/voice")
async def voice(file: UploadFile | None = File(None), transcript: str | None = Form(None)):
    """Accept either an audio file (requires whisper) OR a pre-transcribed string."""
    if transcript:
        return extract_from_transcript(transcript, "voice_note")
    if file is None:
        raise HTTPException(400, "Provide `file` (audio) or `transcript` (string).")
    data = await file.read()
    try:
        text = transcribe_audio(data, file.filename or "voice.wav")
    except Exception as e:
        raise HTTPException(400, f"Transcription failed: {e}")
    return extract_from_transcript(text, file.filename or "voice.wav")


# ---------- mock live inbox ----------
# Files dropped into backend/data/inbox/ appear in /api/inbox/poll as new items.

@app.get("/api/inbox/poll")
def poll_inbox():
    items = []
    for p in sorted(INBOX_DIR.glob("*")):
        if p.name.startswith(".") or not p.is_file():
            continue
        items.append({
            "filename": p.name,
            "size": p.stat().st_size,
            "new": p.name not in INBOX_SEEN,
            "kind": (
                "image" if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else
                "pdf"   if p.suffix.lower() == ".pdf" else
                "audio" if p.suffix.lower() in {".mp3", ".m4a", ".wav", ".ogg"} else
                "other"
            ),
        })
    return {"items": items}


# ---------- FX peak analyzer ----------

@app.get("/api/fx/series")
def fx_series(from_ccy: str, to_ccy: str, days: int = 30):
    return {"series": get_fx_series(from_ccy, to_ccy, days)}


@app.get("/api/fx/what-if")
def fx_what_if(amount: float, from_ccy: str, to_ccy: str, days: int = 30):
    return what_if(amount, from_ccy, to_ccy, days)


# ---------- FX watcher (SQLite-backed) ----------

class WatcherCreate(BaseModel):
    from_ccy: str
    to_ccy: str
    target_rate: float
    note: str = ""


@app.post("/api/fx/watcher")
def fx_watcher_create(body: WatcherCreate):
    wid = str(uuid.uuid4())[:8]
    w = {"id": wid, **body.model_dump()}
    db.upsert_watcher(w)
    return w


@app.get("/api/fx/watcher")
def fx_watcher_list():
    out = []
    for w in db.list_watchers():
        check = watcher_check(w["from_ccy"], w["to_ccy"], w["target_rate"])
        out.append({**w, **check})
    return {"watchers": out}


@app.delete("/api/fx/watcher/{wid}")
def fx_watcher_delete(wid: str):
    db.delete_watcher(wid)
    return {"ok": True}


# ---------- Sales validator ----------

@app.post("/api/sales/validate")
def sales_validate(proof: dict):
    return validate_submission(proof)


# ---------- Audit defense pack ----------

@app.get("/api/audit-pack/{recon_id}/{match_index}")
def audit_pack(recon_id: str, match_index: int):
    from . import attestation as _att
    s = db.load_session(recon_id)
    if not s:
        raise HTTPException(404, "session not found")
    matches = s["matches"]
    if match_index >= len(matches):
        raise HTTPException(404, "match index out of range")
    try:
        pdf = build_audit_pack(matches[match_index], s["bank"],
                               recon_id=recon_id, match_index=match_index)
    except _att.SourceBytesTampered as e:
        # F11: refuse to issue when the underlying proof bytes changed.
        raise HTTPException(422, f"source bytes tampered: {e}")
    inv = matches[match_index]["proof"].get("reference", f"m{match_index}")
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="audit_{inv}.pdf"'},
    )


# ---------- audit pack attestation verification ----------

@app.get("/api/audit-pack/public-key")
def audit_pack_public_key():
    """Public key anyone can use to verify Treasury Oracle audit packs."""
    from . import attestation as _att
    return {
        "algorithm": "Ed25519",
        "fingerprint": _att.public_key_fingerprint(),
        "raw_b64": __import__("base64").b64encode(_att.public_key_bytes()).decode("ascii"),
        "pem": _att.public_key_pem(),
    }


class VerifyAttestation(BaseModel):
    manifest: dict
    signature_b64: str


@app.post("/api/audit-pack/verify")
def audit_pack_verify(body: VerifyAttestation):
    """Verify a manifest + signature. Returns whether the signature is valid
    AND (if a source SHA is present) whether the source bytes still hash
    to that value."""
    from . import attestation as _att
    sig_ok = _att.verify_signature(body.manifest, body.signature_b64)
    sha = body.manifest.get("proof_source_sha256")
    source_status = {"present": False, "verified": False}
    if sha:
        try:
            source_status = _att.verify_source_bytes(sha)
        except _att.SourceBytesTampered as e:
            source_status = {"present": True, "verified": False, "reason": str(e)}
    return {
        "signature_valid": sig_ok,
        "source_bytes": source_status,
        "issuer_fingerprint": _att.public_key_fingerprint(),
        "claimed_fingerprint": body.manifest.get("issuer_key_fingerprint"),
        "all_valid": sig_ok and (source_status.get("verified") or not sha),
    }


# ---------- Dunning campaigns ----------

class CampaignCreate(BaseModel):
    client_name: str
    invoice_ref: str
    invoice_amount: float
    invoice_ccy: str
    outstanding: float
    due_date: str | None = None


@app.post("/api/campaign")
def campaign_create(body: CampaignCreate):
    return create_campaign(**body.model_dump())


@app.get("/api/campaign")
def campaign_list():
    return {"campaigns": list_campaigns()}


@app.post("/api/campaign/{cid}/advance")
def campaign_advance(cid: str):
    try:
        return advance_campaign(cid)
    except KeyError:
        raise HTTPException(404, "campaign not found")


@app.post("/api/campaign/{cid}/paid")
def campaign_paid(cid: str):
    try:
        return mark_paid(cid)
    except KeyError:
        raise HTTPException(404, "campaign not found")


# ---------- Autonomous LangGraph campaign workflow ----------

@app.post("/api/campaign/{cid}/workflow/start")
def campaign_workflow_start(cid: str):
    if not db.get_campaign(cid):
        raise HTTPException(404, "campaign not found")
    return cwf.start(cid)


@app.post("/api/campaign/{cid}/workflow/tick")
def campaign_workflow_tick(cid: str):
    return cwf.tick(cid)


@app.get("/api/campaign/{cid}/workflow/state")
def campaign_workflow_state(cid: str):
    s = cwf.get_state(cid)
    if s is None:
        raise HTTPException(404, "workflow not started")
    return s


@app.post("/api/campaign/{cid}/workflow/stop")
def campaign_workflow_stop(cid: str):
    return cwf.stop(cid)


@app.post("/api/campaign/{cid}/workflow/recover")
def campaign_workflow_recover(cid: str):
    return cwf.recover(cid)


# ---------- Boss documentary ----------

class DocReq(BaseModel):
    amount: float; from_ccy: str; to_ccy: str
    invoice_date: str; payment_date: str
    rate_invoice: float; rate_payment: float
    diff_local: float


@app.post("/api/boss-documentary")
def boss_documentary(body: DocReq):
    return documentary_narrative(**body.model_dump())


# ---------- One-click month-end close ----------

class MonthEndReq(BaseModel):
    bank: str = "default"


@app.post("/api/month-end-close")
def month_end_close(body: MonthEndReq):
    """Batch: ingest every inbox file → returns proofs ready for reconcile."""
    proofs = []
    for p in sorted(INBOX_DIR.glob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        try:
            data = p.read_bytes()
            if p.suffix.lower() == ".pdf":
                proofs.extend([extract_payment_proof(img, f"{p.name}#p{i+1}")
                               for i, img in enumerate(pdf_to_images(data))])
            elif p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                proofs.append(extract_payment_proof(data, p.name))
            INBOX_SEEN.add(p.name)
        except Exception as e:
            proofs.append({"source_file": p.name, "error": str(e)})

    return {
        "ingested_proofs": len(proofs),
        "proofs": proofs,
        "next_step": "Upload a bank statement and click Reconcile to complete the close.",
    }


@app.post("/api/inbox/ingest/{filename}")
def inbox_ingest(filename: str):
    p = _safe_inbox_path(filename)
    if not p.exists():
        raise HTTPException(404, "file not in inbox")
    INBOX_SEEN.add(filename)
    data = p.read_bytes()
    if p.suffix.lower() == ".pdf":
        results = [extract_payment_proof(img, f"{filename}#p{i+1}")
                   for i, img in enumerate(pdf_to_images(data))]
    else:
        results = [extract_payment_proof(data, filename)]
    return {"proofs": results}
