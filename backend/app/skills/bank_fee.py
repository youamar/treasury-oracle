"""Skill: apply_bank_fee — inbound-conversion fee application."""
from __future__ import annotations

from ._base import SkillDef, SkillContext, register
from ..tools import apply_bank_fee


DEFAULT_PROMPT = (
    "Apply the destination bank's inbound-conversion fee after FX conversion. "
    "The net value should match the bank statement's actual credited amount."
)


def handler(ctx: SkillContext, amount: float, bank_name: str = "default") -> dict:
    return apply_bank_fee(float(amount), bank_name)


SKILL = register(SkillDef(
    id="apply_bank_fee",
    name="Bank Fee Application",
    kind="tool",
    description=(
        "Apply the local bank's inbound-conversion fee to a gross amount. "
        "Returns fee_pct, fee_amount, and net_amount after the fee."
    ),
    default_system_prompt=DEFAULT_PROMPT,
    handler=handler,
    parameters={
        "type": "object",
        "properties": {
            "amount": {"type": "number"},
            "bank_name": {"type": "string"},
        },
        "required": ["amount", "bank_name"],
    },
    category="reconciliation",
    tags=("fees", "agent-tool"),
    examples=[{
        "args": {"amount": 4720.0, "bank_name": "Maybank"},
        "result": {"bank": "Maybank", "fee_pct": 0.005, "fee_amount": 23.6,
                   "net_amount": 4696.4, "source": "db:banks/Maybank"},
        "when": "compute net after applying Maybank's 0.5% inbound conversion fee",
    }],
    error_hint=(
        "apply_bank_fee takes (amount: number, bank_name: string). "
        "amount is the GROSS local-currency value AFTER fx conversion "
        "(not the proof's foreign-currency amount). bank_name must match "
        "the destination bank id from the run config — pass 'default' if "
        "unsure rather than inventing a name."
    ),
))
