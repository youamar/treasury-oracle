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
    examples=[{
        "args": {"from_ccy": "USD", "to_ccy": "MYR", "date": "2026-05-20"},
        "result": {"rate": 4.72, "source": "ecb_live", "trusted": True,
                   "asof": "2026-05-20"},
        "when": "convert a USD invoice on its value date to the MYR account",
    }],
    error_hint=(
        "get_fx_rate takes three string args: from_ccy, to_ccy, date. "
        "Currencies are ISO 4217 (3 letters, uppercase, e.g. 'USD', 'MYR'). "
        "date is the proof's value date in 'YYYY-MM-DD' format — never use "
        "today's date or natural-language dates like 'yesterday'."
    ),
    triggers={
        # FX lookups are pointless when the proof is already in the bank's
        # local currency. Pruning these saves ~150 tokens from the prompt
        # AND removes a tempting wrong tool the LLM might call out of habit.
        "cross_currency_only": True,
    },
))
