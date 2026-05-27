"""Memory tool-skills — agent-callable long-term memory over the tenant's history.

These two skills give the agent durable, SQL-backed memory:

  remember_fact(subject, predicate, value)
      Persist a learned fact, e.g. ("Acme Corp", "pays_late_by_days", "5").
      Upserts on (tenant, subject, predicate).

  recall_facts(subject?, predicate?)
      Retrieve recent matching facts. Substring match on subject/predicate.
      Returns at most 20 rows.

Why SQL instead of vector RAG: treasury facts are short, structured and looked
up by exact keys (payer, invoice, currency). Embeddings add latency without
helping retrieval quality here. A vector layer can be added later if the
customer's memory grows into free-form prose.
"""
from __future__ import annotations

from ._base import SkillDef, SkillContext, register
from .. import db


# ---------- remember_fact ----------

_REMEMBER_PROMPT = (
    "Use remember_fact to persist a non-obvious learning about a payer, bank, "
    "currency pair, or invoice pattern that future runs will benefit from. "
    "Examples: ('Acme Corp', 'pays_late_by_days', '5'); "
    "('BankXYZ', 'inbound_fee_pct', '0.012'); "
    "('SGD->MYR', 'preferred_route', 'DBS_direct'). "
    "Do NOT use this for one-off observations — only for patterns you want "
    "the system to remember next month."
)


def _remember_handler(ctx: SkillContext, subject: str, predicate: str,
                      value: str, confidence: float = 1.0) -> dict:
    rec = db.remember_fact(
        subject=subject, predicate=predicate, value=str(value),
        source=ctx.session_id, confidence=float(confidence),
    )
    return {"ok": True, "stored": {
        "subject": rec.get("subject"), "predicate": rec.get("predicate"),
        "value": rec.get("value"), "confidence": rec.get("confidence"),
    }}


register(SkillDef(
    id="remember_fact",
    name="Remember Fact",
    kind="tool",
    description=(
        "Persist a structured learning (subject, predicate, value) into the "
        "platform's long-term memory for this tenant. Upserts on subject+predicate."
    ),
    default_system_prompt=_REMEMBER_PROMPT,
    handler=_remember_handler,
    parameters={
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "Entity (e.g. payer, bank, currency pair)"},
            "predicate": {"type": "string", "description": "Attribute name in snake_case"},
            "value": {"type": "string"},
            "confidence": {"type": "number", "description": "0-1; default 1.0"},
        },
        "required": ["subject", "predicate", "value"],
    },
    default_enabled=True,
    category="memory",
    tags=("memory", "agent-tool"),
    examples=[{
        "args": {"subject": "Acme Corp", "predicate": "pays_late_by_days",
                 "value": "5", "confidence": 0.9},
        "result": {"ok": True, "stored": {"subject": "Acme Corp",
                   "predicate": "pays_late_by_days", "value": "5",
                   "confidence": 0.9}},
        "when": "after observing Acme consistently pays ~5 days past due",
    }],
    error_hint=(
        "remember_fact takes (subject: string, predicate: string, value: string, "
        "confidence?: number). Value is ALWAYS a string — even numeric facts like "
        "'5' or '0.012'. predicate is snake_case (e.g. 'pays_late_by_days', "
        "'inbound_fee_pct'). Do not pass dicts or lists."
    ),
))


# ---------- recall_facts ----------

_RECALL_PROMPT = (
    "Use recall_facts BEFORE making a decision, to check whether the platform "
    "has remembered anything about this payer, bank, or currency pair. "
    "Pass a substring of the subject (e.g. 'Acme') or predicate "
    "(e.g. 'pays_late') — both are optional and combined with AND."
)


def _recall_handler(ctx: SkillContext, subject: str | None = None,
                    predicate: str | None = None, limit: int = 20) -> dict:
    rows = db.recall_facts(subject=subject, predicate=predicate, limit=int(limit))
    return {
        "count": len(rows),
        "facts": [
            {"subject": r["subject"], "predicate": r["predicate"],
             "value": r["value"], "confidence": r["confidence"],
             "learned_at": r["created_at"]}
            for r in rows
        ],
    }


register(SkillDef(
    id="recall_facts",
    name="Recall Facts",
    kind="tool",
    description=(
        "Retrieve previously-remembered facts for this tenant. Substring "
        "match on subject and/or predicate. Returns up to 20 rows, recent first."
    ),
    default_system_prompt=_RECALL_PROMPT,
    handler=_recall_handler,
    parameters={
        "type": "object",
        "properties": {
            "subject": {"type": "string"},
            "predicate": {"type": "string"},
            "limit": {"type": "integer"},
        },
    },
    default_enabled=True,
    category="memory",
    tags=("memory", "agent-tool"),
    examples=[{
        "args": {"subject": "Acme"},
        "result": {"count": 1, "facts": [{"subject": "Acme Corp",
                   "predicate": "pays_late_by_days", "value": "5",
                   "confidence": 0.9, "learned_at": "2026-04-30T10:12:00Z"}]},
        "when": "at the start of a proof: surface what we already know about this payer",
    }],
    error_hint=(
        "recall_facts takes optional (subject: string, predicate: string, "
        "limit: integer). Both subject and predicate are SUBSTRING matches — "
        "passing 'Acme' will find 'Acme Corp', 'Acme Global Holdings', etc. "
        "Either one alone is allowed; omit predicate if you only know the payer."
    ),
))
