"""LLM-backed second-pass verifier for strict reconciliation decisions.

The deterministic skeptic in agent.py catches the 5 most common
overconfidence patterns (diff > 0.5%, date gap, no tool calls, no ref
overlap, no payer overlap) and downgrades to soft when any fire. This
module adds an *independent* LLM auditor as a second opinion.

Ensemble policy (final verdict):
    deterministic = confirm AND llm = confirm  → confirm  (strict stays)
    deterministic = confirm AND llm = reject   → downgrade to soft
    deterministic = downgrade                  → downgrade  (already caught)

Tenants can disable the LLM pass entirely via platform_config
{"verifier_llm_enabled": false} — useful for cost-sensitive workloads.
The deterministic skeptic always runs.
"""
from __future__ import annotations

import json
from typing import Any

from .chutes_client import get_client
from .config import MODEL_PROFILES
from . import reliability


VERIFIER_SYSTEM = """You are an independent treasury auditor. A reconciliation agent has
proposed a STRICT match between a payment proof and a bank transaction.
Your job is to find a reason the match could be wrong.

Be skeptical. Common failure modes you must check:
  - Amount drift larger than what FX + bank fees explain
  - Date gap that suggests this is a *different* payment
  - Payer name on proof doesn't credibly match the bank narrative
  - Reference / invoice number doesn't appear in the txn metadata
  - FX rate the agent used wasn't from a trusted live source
  - Bank fee assumption looks too high or too low for the institution

If you find any credible concern, REJECT the strict match. Otherwise CONFIRM.
Do NOT confirm on weak evidence ("looks fine"). Demand specifics.

Return STRICT JSON only:

{
  "verdict": "confirm" | "reject",
  "concerns": [<short specific strings>],
  "reasoning": "<one short sentence>"
}"""


def _build_user_prompt(decision: dict, proof: dict, chosen: dict, bank: str) -> str:
    tool_calls = decision.get("tool_calls") or []
    tool_summary = []
    for tc in tool_calls:
        name = tc.get("name")
        args = tc.get("arguments") or {}
        result = tc.get("result") or {}
        tool_summary.append(f"  - {name}({json.dumps(args)}) → {json.dumps(result, default=str)[:200]}")
    return (
        f"BANK: {bank}\n\n"
        f"PAYMENT PROOF:\n{json.dumps(proof, ensure_ascii=False, default=str)}\n\n"
        f"BANK TRANSACTION (the agent's chosen match):\n"
        f"{json.dumps(chosen, ensure_ascii=False, default=str)}\n\n"
        f"AGENT'S TOOL CALLS DURING DECISION:\n"
        + ("\n".join(tool_summary) if tool_summary else "  (none — agent answered from priors)")
        + f"\n\nAGENT'S CLAIMED OUTCOME:\n"
        f"  decision        = {decision.get('decision')}\n"
        f"  fx_rate         = {decision.get('fx_rate')}\n"
        f"  fee_amount      = {decision.get('fee_amount')}\n"
        f"  expected_net    = {decision.get('expected_net')}\n"
        f"  actual_received = {decision.get('actual')}\n"
        f"  confidence      = {decision.get('confidence')}\n"
        f"  reasoning       = {decision.get('reasoning')}\n"
    )


def _strip_fences(text: str) -> str:
    from .chutes_client import strip_code_fences
    return strip_code_fences(text)


def llm_verify(decision: dict, proof: dict, chosen: dict, bank: str,
               model_profile: str = "default",
               temperature: float = 0.0,
               timeout: float = 20.0) -> dict:
    """Independent LLM auditor. Returns a verifier-shaped dict:
        {ran, verdict ('confirm'|'reject'|'skip'), concerns, reasoning, method}

    Always returns — failures degrade to verdict='skip' so callers can fall
    back to the deterministic skeptic alone.
    """
    if not chosen or decision.get("decision") != "strict":
        return {"ran": False, "verdict": "skip", "concerns": [],
                "reasoning": "", "method": "llm_verifier_v1"}

    model = MODEL_PROFILES.get(model_profile, MODEL_PROFILES["default"])
    client = get_client(False)

    messages = [
        {"role": "system", "content": VERIFIER_SYSTEM},
        {"role": "user", "content": _build_user_prompt(decision, proof, chosen, bank)},
    ]

    try:
        resp = reliability.with_retry(
            lambda: client.chat.completions.create(
                model=model, messages=messages,
                temperature=temperature,
                max_tokens=400,
                response_format={"type": "json_object"},
                timeout=timeout,
            ),
            source="verifier.llm",
        )
    except Exception as e:
        reliability.record_error("verifier.llm", e,
                                 context={"decision": decision.get("decision"),
                                          "proof_ref": proof.get("reference")})
        return {"ran": True, "verdict": "skip", "concerns": [],
                "reasoning": f"verifier unavailable: {type(e).__name__}",
                "method": "llm_verifier_v1", "error": True}

    raw = _strip_fences((resp.choices[0].message.content or "").strip())
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"ran": True, "verdict": "skip", "concerns": [],
                "reasoning": "verifier returned non-JSON", "method": "llm_verifier_v1",
                "error": True, "raw": raw[:200]}

    verdict = data.get("verdict")
    if verdict not in ("confirm", "reject"):
        return {"ran": True, "verdict": "skip", "concerns": [],
                "reasoning": "verifier returned invalid verdict", "method": "llm_verifier_v1",
                "error": True}

    return {
        "ran": True,
        "verdict": verdict,
        "concerns": list(data.get("concerns") or []),
        "reasoning": str(data.get("reasoning") or "")[:300],
        "method": "llm_verifier_v1",
    }


def merge_verifiers(deterministic: dict, llm: dict) -> dict:
    """Combine the two verifier passes. The strict claim survives only when
    BOTH passes confirm; either one downgrading kicks it down to soft."""
    det_concerns = deterministic.get("concerns") or []
    llm_concerns = llm.get("concerns") or []

    # If LLM was skipped (disabled or errored), defer entirely to deterministic.
    if not llm.get("ran") or llm.get("verdict") == "skip":
        return {
            **deterministic,
            "llm_verifier": llm,  # attach for transparency
            "ensemble": "deterministic_only",
        }

    # If deterministic already downgraded, keep it (no need to confirm).
    if deterministic.get("verdict") == "downgrade":
        return {
            **deterministic,
            "llm_verifier": llm,
            "ensemble": "deterministic_downgrade",
            # If LLM also rejected, surface its concerns too.
            "concerns": det_concerns + [f"[llm] {c}" for c in llm_concerns] if llm.get("verdict") == "reject" else det_concerns,
        }

    # Deterministic confirmed; let LLM be the decider.
    if llm.get("verdict") == "reject":
        return {
            "ran": True,
            "verdict": "downgrade",
            "concerns": [f"[llm] {c}" for c in llm_concerns] or
                        [f"[llm] {llm.get('reasoning')}"],
            "method": "ensemble(deterministic_v1 + llm_v1)",
            "llm_verifier": llm,
            "deterministic_verifier": deterministic,
            "ensemble": "llm_downgrade",
        }

    # Both confirm — strongest possible signal.
    return {
        "ran": True,
        "verdict": "confirm",
        "concerns": [],
        "method": "ensemble(deterministic_v1 + llm_v1)",
        "llm_verifier": llm,
        "deterministic_verifier": deterministic,
        "ensemble": "both_confirmed",
    }
