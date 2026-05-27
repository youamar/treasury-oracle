"""Skill: trace_swift_route — correspondent-bank route inference."""
from __future__ import annotations

from ._base import SkillDef, SkillContext, register
from ..swift import trace_route


DEFAULT_PROMPT = (
    "Use trace_swift_route when actual received is materially less than expected "
    "net. It infers the correspondent-bank chain and attributes the gap to "
    "specific hops, so the report can name which bank ate the fee."
)


def handler(ctx: SkillContext, source_currency: str, sent_amount: float,
            expected_net_local: float, actual_net_local: float,
            fx_rate: float, local_currency: str = "MYR") -> dict:
    return trace_route(
        source_currency=source_currency,
        sent_amount=float(sent_amount),
        expected_net_local=float(expected_net_local),
        actual_net_local=float(actual_net_local),
        fx_rate=float(fx_rate),
        local_currency=local_currency,
    )


SKILL = register(SkillDef(
    id="trace_swift_route",
    name="SWIFT Route Tracer",
    kind="tool",
    description=(
        "Infer the correspondent-bank routing chain when actual received is "
        "materially less than the expected net. Returns ordered route nodes "
        "with per-hop fee attribution."
    ),
    default_system_prompt=DEFAULT_PROMPT,
    handler=handler,
    parameters={
        "type": "object",
        "properties": {
            "source_currency": {"type": "string"},
            "sent_amount": {"type": "number"},
            "expected_net_local": {"type": "number"},
            "actual_net_local": {"type": "number"},
            "fx_rate": {"type": "number"},
            "local_currency": {"type": "string"},
        },
        "required": ["source_currency", "sent_amount", "expected_net_local",
                     "actual_net_local", "fx_rate", "local_currency"],
    },
    category="reconciliation",
    tags=("swift", "agent-tool"),
    model_profile="strong",
    examples=[{
        "args": {"source_currency": "USD", "sent_amount": 1000,
                 "expected_net_local": 4696.4, "actual_net_local": 4661.4,
                 "fx_rate": 4.72, "local_currency": "MYR"},
        "result": {"route": [{"bank": "JPMorgan Chase NY", "fee": 25.0,
                              "role": "correspondent"}],
                   "attributed_fee_local": 35.0, "unexplained_gap_local": 0.0},
        "when": "explain why USD->MYR settlement came in 35 MYR short",
    }],
    error_hint=(
        "trace_swift_route takes six args: source_currency, sent_amount, "
        "expected_net_local, actual_net_local, fx_rate, local_currency. "
        "All amounts are numbers (not strings). sent_amount is in the "
        "PROOF currency; expected_net_local + actual_net_local are in the "
        "BANK's local currency. fx_rate is the multiplier from source to "
        "local (sent_amount * fx_rate ≈ expected_gross_local before fees)."
    ),
    triggers={
        # SWIFT correspondent-bank inference is only meaningful for cross-
        # border payments. Same-currency proofs go through ACH/local rails,
        # no SWIFT hops to attribute.
        "cross_currency_only": True,
    },
))
