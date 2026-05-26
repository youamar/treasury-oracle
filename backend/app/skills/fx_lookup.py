"""Skill: fx_lookup — historical FX rate lookup for the agent loop."""
from __future__ import annotations

from ._base import SkillDef, SkillContext, register
from ..tools import get_fx_rate_full


DEFAULT_PROMPT = (
    "Use get_fx_rate to convert proof amounts into the bank's local currency on "
    "the proof date. Always prefer the proof's value date over today's date. "
    "The response includes a `source` and `trusted` flag — if `trusted` is "
    "false (static_fallback or identity_fallback), DO NOT return decision='strict'. "
    "Downgrade to 'soft' or 'discrepancy' and mention the FX source in your reasoning."
)


def handler(ctx: SkillContext, from_ccy: str, to_ccy: str, date: str) -> dict:
    return get_fx_rate_full(from_ccy, to_ccy, date)


SKILL = register(SkillDef(
    id="get_fx_rate",
    name="FX Rate Lookup",
    kind="tool",
    description=(
        "Fetch the historical foreign-exchange rate between two ISO 4217 currency "
        "codes on a specific date. Returns the multiplier such that "
        "amount_in_from * rate = amount_in_to."
    ),
    default_system_prompt=DEFAULT_PROMPT,
    handler=handler,
    parameters={
        "type": "object",
        "properties": {
            "from_ccy": {"type": "string"},
            "to_ccy": {"type": "string"},
            "date": {"type": "string", "description": "YYYY-MM-DD"},
        },
        "required": ["from_ccy", "to_ccy", "date"],
    },
    category="reconciliation",
    tags=("fx", "agent-tool"),
))
