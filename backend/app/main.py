"""FastAPI entrypoint — Global Treasury Agent."""
import os
import uuid
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from .ocr import extract_payment_proof, pdf_to_images
from .parser import parse_bank_statement
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
from .documentary import documentary_narrative
from . import db

app = FastAPI(title="Global Treasury Agent", version="0.3.0")


@app.on_event("startup")
def _startup():
    db.init_db()

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

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
    out = []
    for f in files:
        data = await f.read()
        name = f.filename or "upload"
        try:
            if name.lower().endswith(".pdf"):
                images = pdf_to_images(data)
                for i, img in enumerate(images):
                    out.append(extract_payment_proof(img, f"{name}#p{i+1}"))
            else:
                out.append(extract_payment_proof(data, name))
        except Exception as e:
            out.append({"source_file": name, "error": str(e)})
    return {"proofs": out}


@app.post("/api/parse-statement")
async def parse_statement(file: UploadFile = File(...)):
    data = await file.read()
    try:
        txns = parse_bank_statement(data, file.filename or "statement.csv")
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"transactions": txns}


class ReconcileRequest(BaseModel):
    proofs: list[dict]
    transactions: list[dict]
    bank: str = "default"
    mode: str = "agent"  # "agent" | "classical"


@app.post("/api/reconcile")
async def reconcile_endpoint(body: ReconcileRequest):
    if body.mode == "classical":
        result = reconcile_classical(body.proofs, body.transactions, body.bank)
        recon_id = str(uuid.uuid4())[:8]
        result["mode"] = "classical"
        db.save_session(recon_id, body.bank, result)
        return {"recon_id": recon_id, **result}
    # agent mode (default)
    result = reconcile_agent(body.proofs, body.transactions, body.bank)
    return result


@app.get("/api/session/{recon_id}")
def get_session(recon_id: str):
    s = db.load_session(recon_id)
    if not s:
        raise HTTPException(404, "session not found")
    return s


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


@app.post("/api/soft-match/confirm")
def confirm_soft(body: ConfirmSoftMatch):
    """User confirmed a soft match — remember the alias AND promote to strict match."""
    remember_alias(body.canonical_payer, body.observed_in_txn)
    promoted = None
    if body.soft_match_id is not None:
        promoted = db.promote_soft_match(body.soft_match_id)
    return {
        "ok": True,
        "remembered": [body.canonical_payer, body.observed_in_txn],
        "session": promoted,
    }


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
    s = db.load_session(recon_id)
    if not s:
        raise HTTPException(404, "session not found")
    matches = s["matches"]
    if match_index >= len(matches):
        raise HTTPException(404, "match index out of range")
    pdf = build_audit_pack(matches[match_index], s["bank"])
    inv = matches[match_index]["proof"].get("reference", f"m{match_index}")
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="audit_{inv}.pdf"'},
    )


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
