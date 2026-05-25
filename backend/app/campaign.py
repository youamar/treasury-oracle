"""Dunning escalation campaign — multi-stage cadence per overdue invoice."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from .chutes_client import chat
from .config import REASONING_MODEL
from .dunning import LANG_MAP
from . import db


STAGES = [
    {"day": 1,  "tone": "warm, gentle, blame the intermediary banks",
     "subject_hint": "Friendly nudge"},
    {"day": 3,  "tone": "polite but firmer, clearly state the outstanding amount",
     "subject_hint": "Following up"},
    {"day": 7,  "tone": "formal, mention CC'ing finance manager, request action by end of week",
     "subject_hint": "Action required"},
    {"day": 14, "tone": "demand letter — formal, reference contract terms, mention next escalation",
     "subject_hint": "Final notice before escalation"},
]

STAGE_PROMPT = """You are an accounts-receivable specialist drafting stage {stage_n} of {stage_total}
of a dunning campaign. Write in {language}. Max 130 words.

Tone for this stage: {tone}
Client: {client_name}
Invoice: {invoice_ref} for {invoice_amount} {invoice_ccy}, due {due_date}
Outstanding: {outstanding} {invoice_ccy}
Days overdue: {days_overdue}

Return JSON ONLY: {{"subject": "...", "body": "..."}}"""


def _draft_stage(stage_idx: int, campaign: dict) -> dict:
    stage = STAGES[stage_idx]
    language = LANG_MAP.get(campaign["invoice_ccy"].upper(), "English")
    prompt = STAGE_PROMPT.format(
        stage_n=stage_idx + 1, stage_total=len(STAGES),
        language=language, tone=stage["tone"],
        client_name=campaign["client_name"],
        invoice_ref=campaign["invoice_ref"],
        invoice_amount=campaign["invoice_amount"],
        invoice_ccy=campaign["invoice_ccy"],
        due_date=campaign["due_date"],
        outstanding=campaign["outstanding"],
        days_overdue=stage["day"],
    )
    try:
        resp = chat(
            messages=[{"role": "user", "content": prompt}],
            model=REASONING_MODEL, temperature=0.5, max_tokens=400,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
            raw = raw.strip()
        d = json.loads(raw)
    except Exception:
        d = {"subject": f"{stage['subject_hint']} — {campaign['invoice_ref']}",
             "body": f"Dear {campaign['client_name']}, invoice {campaign['invoice_ref']} for "
                     f"{campaign['outstanding']} {campaign['invoice_ccy']} remains outstanding. "
                     f"Kindly settle at your earliest convenience."}
    d["stage_index"] = stage_idx
    d["stage_day"] = stage["day"]
    d["language"] = language
    d["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return d


def create_campaign(client_name: str, invoice_ref: str, invoice_amount: float,
                    invoice_ccy: str, outstanding: float, due_date: str | None = None) -> dict:
    cid = str(uuid.uuid4())[:8]
    due = due_date or datetime.today().date().isoformat()
    campaign = {
        "id": cid,
        "client_name": client_name, "invoice_ref": invoice_ref,
        "invoice_amount": invoice_amount, "invoice_ccy": invoice_ccy,
        "outstanding": outstanding, "due_date": due,
        "current_stage": 0, "status": "active",
        "history": [],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    first = _draft_stage(0, campaign)
    campaign["history"].append({**first, "sent": False})
    db.upsert_campaign(campaign)
    return campaign


def advance_campaign(cid: str) -> dict:
    c = db.get_campaign(cid)
    if not c: raise KeyError(cid)
    if c["current_stage"] >= len(STAGES) - 1:
        c["status"] = "exhausted"
        db.upsert_campaign(c)
        return c
    c["history"][-1]["sent"] = True
    c["current_stage"] += 1
    nxt = _draft_stage(c["current_stage"], c)
    c["history"].append({**nxt, "sent": False})
    db.upsert_campaign(c)
    return c


def mark_paid(cid: str) -> dict:
    c = db.get_campaign(cid)
    if not c: raise KeyError(cid)
    c["status"] = "paid"
    db.upsert_campaign(c)
    return c


def list_campaigns() -> list[dict]:
    return db.list_campaigns_db()
