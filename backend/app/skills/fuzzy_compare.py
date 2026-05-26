"""Skill: fuzzy_compare — payer/invoice/alias similarity scoring."""
from __future__ import annotations

from ._base import SkillDef, SkillContext, register
from ..fuzzy import soft_match_score


DEFAULT_PROMPT = (
    "Use fuzzy_compare when the amount tier alone is ambiguous. It returns a "
    "similarity score plus the contributing signals (payer name, invoice ref, "
    "learned alias). Trust scores >= 0.7 with at least two strong signals."
)


def handler(ctx: SkillContext, proof_index: int, txn_index: int) -> dict:
    candidates = ctx.extras.get("candidates", [])
    proof = ctx.extras.get("proof") or {}
    ti = int(txn_index)
    if ti < 0 or ti >= len(candidates):
        return {"error": f"txn_index {ti} out of range 0..{len(candidates)-1}"}
    return soft_match_score(proof, candidates[ti])


SKILL = register(SkillDef(
    id="fuzzy_compare",
    name="Fuzzy Comparator",
    kind="tool",
    description=(
        "Compare a payment proof to a bank transaction using payer-name "
        "similarity, invoice-reference overlap, and learned-alias memory."
    ),
    default_system_prompt=DEFAULT_PROMPT,
    handler=handler,
    parameters={
        "type": "object",
        "properties": {
            "proof_index": {"type": "integer"},
            "txn_index": {"type": "integer"},
        },
        "required": ["proof_index", "txn_index"],
    },
    category="reconciliation",
    tags=("matching", "agent-tool"),
    model_profile="cheap",
))
