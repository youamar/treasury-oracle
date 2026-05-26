"""Capability skills — exposed via API endpoints, not via the LLM tool loop.

Each capability has an editable system_prompt that the platform owner can tune
in the /settings UI. The handler receives a SkillContext (with the resolved
system_prompt under ctx.skill_config['system_prompt']) plus its own kwargs.

For most capabilities the system_prompt is informational — the actual handler
already implements the behavior. The wizard reads/writes these prompts to
let the customer reshape tone, language defaults, escalation cadence, etc.
"""
from __future__ import annotations

from ._base import SkillDef, SkillContext, register
from .. import dunning as _dunning
from .. import campaign as _campaign
from .. import documentary as _documentary
from .. import audit_pack as _audit
from .. import report as _report
from .. import voice as _voice
from .. import validator as _validator
from .. import fx_history as _fxh


# ---------- Dunning ----------
def _dunning_handler(ctx: SkillContext, **kwargs):
    return _dunning.generate_dunning(**kwargs)


register(SkillDef(
    id="dunning_email",
    name="Auto-Dunning Email",
    kind="capability",
    description="Generate a multilingual dunning email when a payment short-falls.",
    default_system_prompt=(
        "Draft a polite but firm payment-shortfall notice. Match the client's "
        "language. Lead with the invoice reference, state the shortfall in their "
        "currency, attribute the cause (FX / bank fees / SWIFT route) when known, "
        "and request a top-up by a specific date. Avoid blame."
    ),
    handler=_dunning_handler,
    category="collections",
    tags=("email", "multilingual"),
))


# ---------- Boss chart (FX blame) ----------
def _boss_chart_handler(ctx: SkillContext, **kwargs):
    return _dunning.boss_chart(**kwargs)


register(SkillDef(
    id="boss_fx_chart",
    name="Blame-the-Fed FX Chart",
    kind="capability",
    description="Explain a local-currency shortfall as an FX-rate movement, not lost revenue.",
    default_system_prompt=(
        "Frame currency shortfalls as macroeconomic context (rate move between "
        "invoice and payment date). Keep tone factual; the goal is to defuse "
        "internal blame, not minimize the gap."
    ),
    handler=_boss_chart_handler,
    category="reporting",
))


# ---------- Boss documentary ----------
def _doc_handler(ctx: SkillContext, **kwargs):
    return _documentary.documentary_narrative(**kwargs)


register(SkillDef(
    id="boss_documentary",
    name="Boss Documentary Narrative",
    kind="capability",
    description="Generate a David-Attenborough-style narration of the FX shortfall.",
    default_system_prompt=(
        "Narrate FX events in calm, documentary tone. 3-5 short paragraphs. "
        "Anchor each paragraph to a date + rate. End with the net impact "
        "in local currency."
    ),
    handler=_doc_handler,
    category="reporting",
    tags=("storytelling",),
))


# ---------- Campaigns ----------
def _campaign_create_handler(ctx: SkillContext, **kwargs):
    return _campaign.create_campaign(**kwargs)


register(SkillDef(
    id="dunning_campaign",
    name="4-Stage Dunning Campaign",
    kind="capability",
    description="Spin up a 4-stage escalation campaign for an outstanding invoice.",
    default_system_prompt=(
        "Escalate in 4 stages over 30 days: friendly reminder → firm notice → "
        "manager CC → final demand. Tone hardens each stage but stays "
        "professional. Stop on payment received."
    ),
    handler=_campaign_create_handler,
    category="collections",
))


# ---------- Audit pack ----------
def _audit_handler(ctx: SkillContext, match: dict, bank: str):
    return _audit.build_audit_pack(match, bank)


register(SkillDef(
    id="audit_defense_pack",
    name="Audit Defense Pack",
    kind="capability",
    description="Per-transaction PDF defending the match decision for auditors.",
    default_system_prompt=(
        "Document every input, tool call, and decision criterion that led to the "
        "match. Reader is an external auditor — assume zero context. Include "
        "source-file hash, FX source, fee schedule used, and confidence score."
    ),
    handler=_audit_handler,
    category="compliance",
    tags=("pdf", "audit"),
))


# ---------- Recon report ----------
def _report_handler(ctx: SkillContext, session: dict, bank: str):
    return _report.build_report_pdf(session, bank)


register(SkillDef(
    id="reconciliation_report",
    name="Reconciliation Report",
    kind="capability",
    description="End-of-run PDF summarising matches, soft matches, and discrepancies.",
    default_system_prompt=(
        "Open with totals (matched / soft / unmatched). Group discrepancies by "
        "root cause (FX, fees, SWIFT). Close with action items per outstanding "
        "item."
    ),
    handler=_report_handler,
    category="reporting",
    tags=("pdf",),
))


# ---------- Voice ingest ----------
def _voice_handler(ctx: SkillContext, transcript: str, source: str = "voice_note"):
    return _voice.extract_from_transcript(transcript, source)


register(SkillDef(
    id="voice_ingest",
    name="Voice-Note Ingestion",
    kind="capability",
    description="Turn a transcribed voice note into a structured payment proof.",
    default_system_prompt=(
        "Extract payer, amount, currency, value date, and invoice reference from "
        "informal spoken notes. If a field is ambiguous, leave it null rather "
        "than guess."
    ),
    handler=_voice_handler,
    category="ingest",
))


# ---------- Sales validator ----------
def _validator_handler(ctx: SkillContext, proof: dict):
    return _validator.validate_submission(proof)


register(SkillDef(
    id="sales_validator",
    name="Sales Submission Validator",
    kind="capability",
    description="Validate that a sales-submitted proof has all required fields.",
    default_system_prompt=(
        "Reject proofs missing payer, amount, currency, or value date. Warn on "
        "stale dates (>30d) and unknown currencies. Be strict — bad inputs "
        "poison reconciliation."
    ),
    handler=_validator_handler,
    category="ingest",
))


# ---------- FX peak / what-if ----------
def _fx_what_if_handler(ctx: SkillContext, amount: float, from_ccy: str,
                        to_ccy: str, days: int = 30):
    return _fxh.what_if(amount, from_ccy, to_ccy, days)


register(SkillDef(
    id="fx_peak_analyzer",
    name="Retroactive FX Peak Analyzer",
    kind="capability",
    description="Show what the customer *could* have received at the FX peak.",
    default_system_prompt=(
        "Compare actual conversion vs. the best rate in the window. Output "
        "currency-neutral percentages alongside absolute amounts."
    ),
    handler=_fx_what_if_handler,
    category="analytics",
))


# ---------- FX watcher (jackpot) ----------
def _fx_watcher_handler(ctx: SkillContext, from_ccy: str, to_ccy: str,
                        target_rate: float):
    return _fxh.watcher_check(from_ccy, to_ccy, target_rate)


register(SkillDef(
    id="fx_jackpot_watcher",
    name="FX Jackpot Watcher",
    kind="capability",
    description="Alert when a target FX rate is crossed.",
    default_system_prompt=(
        "Watch a currency pair and notify the moment the target rate is "
        "reached. Notification should include current rate, target rate, and "
        "indicative amount conversion."
    ),
    handler=_fx_watcher_handler,
    category="analytics",
))


# ---------- Month-end close ----------
def _month_end_handler(ctx: SkillContext, ingest_paths: list, extract_fn,
                       pdf_to_images_fn):
    """Caller passes the ingestion primitives so this skill stays IO-agnostic."""
    proofs = []
    for p in ingest_paths:
        try:
            data = p.read_bytes()
            if p.suffix.lower() == ".pdf":
                proofs.extend([extract_fn(img, f"{p.name}#p{i+1}")
                               for i, img in enumerate(pdf_to_images_fn(data))])
            elif p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                proofs.append(extract_fn(data, p.name))
        except Exception as e:
            proofs.append({"source_file": p.name, "error": str(e)})
    return {"ingested_proofs": len(proofs), "proofs": proofs}


register(SkillDef(
    id="month_end_close",
    name="One-Click Month-End Close",
    kind="capability",
    description="Batch-ingest the inbox into proofs ready to reconcile.",
    default_system_prompt=(
        "Sweep the inbox at month-end. Treat unreadable files as errors, not "
        "warnings. Surface the file count and any failed files before passing "
        "to reconciliation."
    ),
    handler=_month_end_handler,
    category="ingest",
))
