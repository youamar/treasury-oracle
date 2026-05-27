"""ReconciliationAgent — config-driven LLM tool-call loop over the skill registry.

The engine no longer hard-codes tools. It loads the enabled tool-kind skills
from the platform config, exposes them to the LLM as function-call tools, and
dispatches each call through SKILL_REGISTRY. Per-skill system_prompts are
composed into the engine prompt so the customer can shape behavior without
touching code.
"""
from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

from . import llm_router
from . import chutes_client
# Re-exported for the test suite's mock_chutes fixture (conftest.py
# monkeypatches `app.agent.get_client` and `app.agent.chutes_client.
# _call_primary`). Production code paths go through chutes_client.chat
# which transparently delegates to llm_router for failover.
from .chutes_client import get_client  # noqa: F401
from .config import REASONING_MODEL, MATCH_TOLERANCE
from . import db
from . import platform_config
from . import reliability
from . import calibration
from .skills import (
    SkillContext, SKILL_REGISTRY, enabled_tool_skills, resolve_skill_config,
)
from .config import BANK_FEES
from . import verifier as _verifier_mod


def _build_provenance(decision: dict, proof: dict, chosen: dict | None,
                      bank: str) -> dict:
    """Build a per-numeric provenance block for a match.

    Every numeric field reported in the match output carries the source it
    came from. This is what makes the audit pack defensible: every number
    can be traced back to ECB / a bank-statement row / the OCR'd proof /
    a config value / or 'agent_unverified' if the LLM made it up.
    """
    tool_calls = decision.get("tool_calls") or []
    fx_calls = [tc for tc in tool_calls
                if tc.get("name") == "get_fx_rate"
                and isinstance(tc.get("result"), dict)]
    fee_calls = [tc for tc in tool_calls
                 if tc.get("name") == "apply_bank_fee"
                 and isinstance(tc.get("result"), dict)]

    # fx_rate
    if fx_calls:
        fx_res = fx_calls[-1]["result"]
        fx_prov = {
            "value": fx_res.get("rate"),
            "source": fx_res.get("source"),
            "asof": fx_res.get("asof"),
            "trusted": bool(fx_res.get("trusted")),
        }
    else:
        # LLM reported an fx_rate without calling the tool — unverified.
        fx_prov = {
            "value": decision.get("fx_rate"),
            "source": "agent_unverified",
            "trusted": False,
        }

    # fee
    if fee_calls:
        fee_res = fee_calls[-1]["result"]
        fee_prov = {
            "value": fee_res.get("fee_amount"),
            "fee_pct": fee_res.get("fee_pct"),
            "source": f"config:BANK_FEES.{fee_res.get('bank', bank)}",
            "trusted": True,
        }
    else:
        # Fall back to the config table directly so we still know where the
        # number came from even if the LLM never invoked the skill.
        cfg_pct = BANK_FEES.get(bank, BANK_FEES["default"])
        fee_prov = {
            "value": decision.get("fee_amount"),
            "fee_pct": cfg_pct,
            "source": (f"config:BANK_FEES.{bank}"
                       if decision.get("fee_amount") is not None
                       else "agent_unverified"),
            "trusted": decision.get("fee_amount") is not None,
        }

    # actual_received — comes from the bank statement row
    actual_prov = {
        "value": (chosen or {}).get("amount"),
        "source": f"bank_statement:{(chosen or {}).get('id')}" if chosen else None,
        "trusted": True,
    }

    # proof amount — from OCR; carries the upload SHA when available
    proof_prov = {
        "value": proof.get("amount"),
        "currency": proof.get("currency"),
        "source": f"ocr:{proof.get('source_file')}",
        "source_sha256": proof.get("source_sha256"),
        "trusted": not bool(proof.get("error")),
    }

    # expected_net / gross — computed, with inputs pointing back at the
    # provenance entries above so a reader can trace the calculation.
    expected_gross_prov = {
        "value": decision.get("fx_rate") and round(
            (decision.get("fx_rate") or 0) * (proof.get("amount") or 0), 2
        ),
        "source": "computed",
        "inputs": ["proof_amount", "fx_rate"],
    }
    expected_net_prov = {
        "value": decision.get("expected_net"),
        "source": "computed" if decision.get("expected_net") is not None else "agent_unverified",
        "inputs": ["expected_gross", "fee_amount"],
        "trusted": (fx_prov["trusted"] and fee_prov["trusted"]
                    and decision.get("expected_net") is not None),
    }

    return {
        "proof_amount": proof_prov,
        "fx_rate": fx_prov,
        "fee": fee_prov,
        "expected_gross": expected_gross_prov,
        "expected_net": expected_net_prov,
        "actual_received": actual_prov,
        # Overall: if any input is untrusted, the whole match is untrusted.
        "all_inputs_trusted": (fx_prov["trusted"] and fee_prov["trusted"]
                               and actual_prov["trusted"] and proof_prov["trusted"]),
    }


MAX_STEPS = 6
DATE_WINDOW_DAYS = 5
DEFAULT_AGENT_TEMPERATURE = 0.1   # production default
EVAL_AGENT_TEMPERATURE = 0.0      # used during eval runs for reproducibility

# Backpressure — bound how many LLM calls the agent is making in flight at
# once. Without this, N concurrent /api/reconcile requests fan out to
# N × proofs × MAX_STEPS concurrent Chutes calls and trip rate limits.
import threading as _threading
import os as _os
AGENT_LLM_CONCURRENCY = int(_os.getenv("AGENT_LLM_CONCURRENCY", "4"))
_agent_llm_semaphore = _threading.BoundedSemaphore(AGENT_LLM_CONCURRENCY)

# --- Auto-disable for skills that keep raising SKILL_BUG -----------------
# When a skill's handler throws an internal exception (NOT a bad-arg from
# the LLM), we track it per-tenant in SQLite (`skill_health` table) with a
# 24h sliding disable window. Two consecutive failures → skill drops out
# of the agent's tool spec until an operator re-enables it. Promoted from
# the old in-memory dict so a skill that's broken for one tenant doesn't
# waste agent tokens on EVERY reconcile run.
_SKILL_AUTODISABLE_THRESHOLD = int(_os.getenv("SKILL_AUTODISABLE_THRESHOLD", "2"))


def _record_skill_failure(session_id: str, skill_id: str, error: str = "") -> int:
    """Bump the persistent failure counter for the current tenant's skill.
    Returns the new consecutive-failure count. `session_id` is no longer
    used for keying but kept in the signature so call sites don't need to
    change — the tenant comes from db._t() (ContextVar)."""
    _ = session_id  # tenant scope is implicit via db._t()
    row = db.record_skill_failure(
        skill_id, threshold=_SKILL_AUTODISABLE_THRESHOLD, error=error,
    )
    return int(row.get("consecutive_failures") or 0)


def _disabled_skills_for(session_id: str) -> set[str]:
    """Skills currently auto-disabled for the active tenant. `session_id`
    accepted for backwards-compat with old callers; the DB is the truth."""
    _ = session_id
    return db.list_disabled_skills()


def _clear_session_skill_failures(session_id: str) -> None:
    """No-op now — disable state is persistent and only an operator
    `reenable_skill` should clear it. Kept so reconcile_agent's existing
    call site doesn't need to be removed."""
    _ = session_id


# --- Trigger-based skill pruning (delta #6) -------------------------------

def _bank_currency(bank: str) -> str | None:
    """Resolve the bank's settlement currency for trigger evaluation.
    Best-effort — returns None if the bank isn't in the registry, which
    just means we don't prune anything for that case (safer than wrong)."""
    try:
        row = db.get_bank(bank)
    except Exception:
        return None
    return (row or {}).get("currency") or None


def _skill_applies(skill, proof: dict, candidates: list,
                   bank_currency: str | None) -> tuple[bool, str]:
    """Decide whether `skill` should be offered for THIS proof.

    Returns (applies, reason_if_pruned). Reason is empty when applies=True
    and used in the trace event when applies=False so the operator can
    see why a tool was hidden from the agent.
    """
    trig = getattr(skill, "triggers", None) or {}
    if not trig:
        return True, ""
    # `requires_candidates` — pruning a candidate-needing skill when there
    # are zero candidates avoids wasted reasoning ("I'd like to fuzzy_compare
    # but there's nothing to compare to").
    if trig.get("requires_candidates") and not candidates:
        return False, "no candidate transactions to compare against"
    # `cross_currency_only` — only useful when the proof currency differs
    # from the bank's settlement currency. Skip FX skills on SGD->SGD etc.
    if trig.get("cross_currency_only"):
        pc = (proof.get("currency") or "").upper()
        bc = (bank_currency or "").upper()
        if pc and bc and pc == bc:
            return False, f"same-currency proof ({pc}) — no FX/SWIFT needed"
    # `applicable_currencies` — whitelist (e.g. "trace_swift_route only on
    # the corridor we have route data for"). Empty list = always applies.
    allow = trig.get("applicable_currencies") or []
    if allow:
        pc = (proof.get("currency") or "").upper()
        if pc and pc not in {c.upper() for c in allow}:
            return False, f"proof currency {pc!r} not in skill's whitelist {allow}"
    return True, ""


# Per-proof LLM dispatch is inlined in `_run_one_proof`:
#   - In tests, `get_client` is monkeypatched to return a scripted stub.
#     We build the client once per proof so its scripted state advances
#     across steps (the lambda creates a fresh state dict each call, so
#     re-creating the client would replay script[0] forever).
#   - In production, `get_client is chutes_client.get_client`, so we route
#     through `chutes_client.chat` for retry/failover/breaker integration.

# Reflection — when the agent's first decision is shaky, we let it re-plan
# once. Triggers below cover the patterns we've actually seen in eval runs.
REFLECTION_CONFIDENCE_THRESHOLD = 0.7
REFLECTION_MAX_CYCLES = 1  # cap so a confused LLM can't burn the step budget

# Verifier (F2) — second-pass skeptic that audits the agent's proposed decision.
VERIFIER_STRICT_DIFF_PCT = 0.005        # >0.5% diff is unusual for a 'strict' claim
VERIFIER_STRICT_DAYS_OFF = 2            # date gap that should be a 'soft' not 'strict'
VERIFIER_MIN_TOOL_CALLS_FOR_STRICT = 1  # an LLM jumping straight to strict is suspect


# Per-tenant overrides — every constant above can be set in platform_config
# under the corresponding key. Resolved once per reconcile_agent call.
AGENT_KNOBS_DEFAULTS = {
    "max_steps":                          MAX_STEPS,
    "date_window_days":                   DATE_WINDOW_DAYS,
    "reflection_confidence_threshold":    REFLECTION_CONFIDENCE_THRESHOLD,
    "reflection_max_cycles":              REFLECTION_MAX_CYCLES,
    "verifier_strict_diff_pct":           VERIFIER_STRICT_DIFF_PCT,
    "verifier_strict_days_off":           VERIFIER_STRICT_DAYS_OFF,
    "verifier_min_tool_calls_for_strict": VERIFIER_MIN_TOOL_CALLS_FOR_STRICT,
    "match_tolerance":                    MATCH_TOLERANCE,
    "agent_temperature":                  DEFAULT_AGENT_TEMPERATURE,
    "verifier_llm_enabled":               True,
    "verifier_model_profile":             "cheap",
    "base_prompt":                        None,  # None → use built-in
}


def _resolve_knobs(platform_cfg: dict, bank: str | None = None) -> dict:
    """Effective per-tenant knobs, with optional per-bank match_tolerance
    override. Resolution order: bank → tenant → module default."""
    knobs = (platform_cfg or {}).get("agent_knobs") or {}
    out = dict(AGENT_KNOBS_DEFAULTS)
    for k, v in knobs.items():
        if k in out and v is not None:
            out[k] = v
    # Per-bank match_tolerance override — bank registry trumps tenant default.
    if bank:
        try:
            bank_row = db.get_bank(bank)
            if bank_row and bank_row.get("match_tolerance") is not None:
                out["match_tolerance"] = float(bank_row["match_tolerance"])
        except Exception:
            pass
    # Legacy compat — earlier code wrote some keys directly on cfg.
    for legacy_key in ("agent_temperature", "verifier_llm_enabled",
                       "verifier_model_profile"):
        if legacy_key in (platform_cfg or {}):
            out[legacy_key] = platform_cfg[legacy_key]
    return out


def _payer_overlap(payer: str | None, txn_desc: str | None) -> bool:
    """True if any payer word (len ≥ 3) appears in the bank narrative."""
    if not payer or not txn_desc:
        return False
    desc_lower = txn_desc.lower()
    return any(w for w in payer.lower().split() if len(w) >= 3 and w in desc_lower)


def _reference_overlap(proof_ref: str | None, txn_ref: str | None,
                       txn_desc: str | None) -> bool:
    if not proof_ref:
        return False
    pr = proof_ref.strip().lower()
    if txn_ref and pr in txn_ref.strip().lower():
        return True
    if txn_desc and pr in txn_desc.lower():
        return True
    return False


def _verify_decision(decision: dict, proof: dict, chosen: dict | None,
                     bank: str, knobs: dict | None = None) -> dict:
    """Deterministic skeptic pass. Thresholds resolved from `knobs`
    (per-tenant) when provided, else module defaults."""
    k = knobs or AGENT_KNOBS_DEFAULTS
    diff_ceiling = float(k["verifier_strict_diff_pct"])
    days_ceiling = int(k["verifier_strict_days_off"])
    min_tools = int(k["verifier_min_tool_calls_for_strict"])

    d = decision.get("decision")
    if d != "strict" or chosen is None:
        return {"ran": False, "verdict": "skip", "concerns": [],
                "method": "programmatic_skeptic_v1"}

    concerns: list[str] = []

    expected = float(decision.get("expected_net") or 0)
    actual = float(decision.get("actual") or chosen.get("amount") or 0)
    if expected > 0:
        diff_pct = abs(actual - expected) / expected
        if diff_pct > diff_ceiling:
            concerns.append(
                f"strict claimed but diff is {diff_pct*100:.2f}% — above "
                f"verifier ceiling {diff_ceiling*100:.2f}%"
            )

    try:
        from datetime import date as _d
        pd_ = _d.fromisoformat(proof.get("date"))
        td_ = _d.fromisoformat(chosen.get("date"))
        days_off = abs((pd_ - td_).days)
        if days_off > days_ceiling:
            concerns.append(
                f"strict claimed but proof and txn are {days_off} days apart "
                f"(verifier wants ≤{days_ceiling})"
            )
    except Exception:
        pass

    tool_calls = decision.get("tool_calls") or []
    n_substantive = sum(
        1 for tc in tool_calls
        if tc.get("name") in {"get_fx_rate", "apply_bank_fee", "fuzzy_compare"}
    )
    if n_substantive < min_tools:
        concerns.append(
            f"strict claimed without any FX/fee/fuzzy tool call — "
            f"agent answered from priors only"
        )

    # 4) Reference mismatch — if the proof has an invoice ref, the txn should
    # mention it somewhere. Strict without ref-overlap is highly suspect.
    if proof.get("reference") and not _reference_overlap(
            proof.get("reference"), chosen.get("reference"),
            chosen.get("description")):
        concerns.append(
            f"strict claimed but proof.reference={proof.get('reference')!r} "
            f"not found in txn reference/description"
        )

    # 5) Payer / narrative mismatch — strict without any payer-name overlap
    # is suspect unless reference matched (already checked above).
    if proof.get("payer") and not _payer_overlap(proof.get("payer"),
                                                 chosen.get("description")):
        # Only escalate if reference ALSO didn't match (else we already
        # know the reference is the bond).
        if proof.get("reference") and _reference_overlap(
                proof.get("reference"), chosen.get("reference"),
                chosen.get("description")):
            pass  # ref carries it
        else:
            concerns.append(
                f"strict claimed but no payer-name overlap between "
                f"{proof.get('payer')!r} and txn narrative"
            )

    return {
        "ran": True,
        "verdict": "downgrade" if concerns else "confirm",
        "concerns": concerns,
        "method": "programmatic_skeptic_v1",
    }


# ---------- Prompt assembly ----------

_BASE_PROMPT = f"""You are a treasury reconciliation agent. For each payment proof,
decide whether it matches one of the candidate bank transactions provided.

Available tools are listed below — each tool has its own usage guidance.

At the start of each proof, call recall_facts(subject=<payer>) and
recall_facts(subject=<bank>) to surface anything the platform already learned.
After the decision, if you discovered a stable pattern (e.g. a payer's
preferred reference format, a bank's actual inbound fee), call remember_fact
to persist it.

Decision policy:
  * If |diff| / expected_net <= {MATCH_TOLERANCE:.2%}, decision = "strict".
  * If diff is between {MATCH_TOLERANCE:.0%} and 15%, call fuzzy_compare for
    non-numerical signals. If strong, decision = "soft".
  * If diff > 15% AND actual < expected, call trace_swift_route to attribute
    the gap. decision = "discrepancy".
  * If no candidate is plausible, decision = "no_match".

When you have a decision, RESPOND WITH FINAL JSON (no tool call):

{{
  "decision": "strict" | "soft" | "discrepancy" | "no_match",
  "txn_index": <int or null>,
  "fx_rate": <number or null>,
  "fee_amount": <number or null>,
  "expected_net": <number or null>,
  "actual": <number or null>,
  "confidence": <0-1>,
  "fuzzy_signals": [<strings>],
  "swift_route": <object or null>,
  "reasoning": "<one short sentence>"
}}

Be decisive. Do not call the same tool twice with identical arguments."""


def _compose_system_prompt(tool_skills: list, platform_cfg: dict) -> str:
    """Merge base prompt with per-skill usage guidance and the tenant's
    own knowledge notes (per-account MEMORY.md). Base prompt is editable
    via platform_config['agent_knobs']['base_prompt']."""
    knobs = (platform_cfg or {}).get("agent_knobs") or {}
    base = knobs.get("base_prompt") or _BASE_PROMPT
    sections = [base]

    # Per-account knowledge file. Surfaces things like "Acme Corp pays from
    # their Singapore holding company", "Maybank charges 0.6% not 0.5% for
    # USD inbound this month", etc. Capped at 4KB so a long doc can't
    # blow out the context window.
    notes = (db.get_tenant_notes().get("content") or "").strip()
    if notes:
        clipped = notes[:4000]
        sections.extend([
            "",
            "ACCOUNT KNOWLEDGE (operator-maintained — treat as ground truth):",
            clipped,
            ("…[truncated]" if len(notes) > 4000 else ""),
        ])

    sections.extend(["", "Tool usage guidance:"])
    for s in tool_skills:
        sc = resolve_skill_config(s, platform_cfg)
        sections.append(f"- {s.id}: {sc.get('system_prompt', '').strip()}")
        # One concrete (args -> result) example per skill, when defined.
        # This is the single highest-leverage thing for cutting "LLM
        # guesses the wrong arg shape and burns a retry" rounds: a worked
        # example beats a schema description every time.
        for ex in (getattr(s, "examples", []) or [])[:1]:
            args_str = json.dumps(ex.get("args", {}), ensure_ascii=False)
            result_str = json.dumps(ex.get("result", {}), ensure_ascii=False)
            if len(result_str) > 140:
                result_str = result_str[:137] + "…"
            when = ex.get("when", "")
            line = f"    e.g. {s.id}({args_str}) -> {result_str}"
            if when:
                line += f"  // {when}"
            sections.append(line)
    return "\n".join(sections)


# ---------- Helpers ----------

def _candidate_txns(proof: dict, all_txns: list[dict], used_ids: set[str]) -> list[dict]:
    pd = proof.get("date")
    if not pd:
        return [t for t in all_txns if t["id"] not in used_ids]
    out = []
    for t in all_txns:
        if t["id"] in used_ids:
            continue
        try:
            days = abs((datetime.fromisoformat(pd) - datetime.fromisoformat(t["date"])).days)
        except Exception:
            days = 999
        if days <= DATE_WINDOW_DAYS:
            out.append(t)
    return out


def _build_user_prompt(proof: dict, candidates: list[dict], bank: str) -> str:
    return (
        f"Bank: {bank}\n\n"
        f"PROOF:\n{json.dumps(proof, ensure_ascii=False)}\n\n"
        f"CANDIDATE TRANSACTIONS (indexed):\n"
        + "\n".join(f"  [{i}] {json.dumps(t, ensure_ascii=False)}"
                    for i, t in enumerate(candidates))
        + "\n\nDecide using your tools, then return the final JSON."
    )


def _extract_final_json(content: str) -> dict | None:
    """Best-effort JSON extraction from raw LLM text. Handles plain JSON,
    fenced ```json blocks, and embedded JSON inside reasoning text (via the
    last-balanced-{} salvage in extract_content). Returns None on failure
    so callers can decide whether to coach the LLM or fall back."""
    from .chutes_client import strip_code_fences, _last_json_block
    if not content:
        return None
    txt = strip_code_fences(content)
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        pass
    # Salvage attempt — find the last balanced {...} block. Reasoning
    # models often emit the JSON answer at the end of a longer narration.
    salvaged = _last_json_block(txt)
    if salvaged:
        try:
            return json.loads(salvaged)
        except json.JSONDecodeError:
            return None
    return None


# ---------- Decision schema ------------------------------------------------
# Pydantic-validate the agent's final JSON before downstream code touches it.
# Catches the three classes of LLM-shape-drift that used to crash the
# verifier / matching path silently:
#   1. wrong case: {"decision": "STRICT"}  -> normalized to "strict"
#   2. missing field: no `confidence` -> defaulted to 0.0 with a coaching note
#   3. wrong type: "txn_index": "0" (string) -> coerced to int when possible

import re as _re
from pydantic import BaseModel, Field, ValidationError, field_validator


_VALID_DECISIONS = {"strict", "soft", "discrepancy", "no_match"}


class _DecisionSchema(BaseModel):
    """Canonical shape the agent must produce. Normalization happens in
    field validators so common LLM stylistic drift (uppercase decision,
    quoted numbers) doesn't fail the call."""
    decision: str
    txn_index: int | None = None
    fx_rate: float | None = None
    fee_amount: float | None = None
    expected_net: float | None = None
    actual: float | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    fuzzy_signals: list[str] = Field(default_factory=list)
    swift_route: dict | None = None
    reasoning: str = ""

    @field_validator("decision")
    @classmethod
    def _normalize_decision(cls, v: str) -> str:
        n = (v or "").strip().lower().replace("-", "_").replace(" ", "_")
        if n not in _VALID_DECISIONS:
            raise ValueError(
                f"decision must be one of {sorted(_VALID_DECISIONS)}, got {v!r}"
            )
        return n

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, v):
        # Some models emit '0.95', '95%' or '95' for confidence. Normalize.
        if v is None or v == "":
            return 0.0
        if isinstance(v, (int, float)):
            return float(v) / 100.0 if v > 1.0 else float(v)
        if isinstance(v, str):
            s = v.strip().rstrip("%")
            try:
                f = float(s)
                return f / 100.0 if f > 1.0 else f
            except ValueError:
                return 0.0
        return 0.0


def _validate_decision(raw: dict) -> tuple[dict | None, str | None]:
    """Validate + normalize the LLM's decision JSON.

    Returns (normalized_dict, None) on success.
    Returns (None, coaching_message) when the LLM's output can't be
    coerced — the caller injects the coaching message back into the
    conversation so the LLM can fix it on the next turn. The coaching
    message names the specific fields that failed."""
    if not isinstance(raw, dict):
        return None, "Your response must be a JSON object, not a list or string."
    try:
        model = _DecisionSchema.model_validate(raw)
    except ValidationError as e:
        problems = []
        for err in e.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "<root>"
            msg = err["msg"]
            problems.append(f"`{loc}`: {msg}")
        coaching = (
            "Your JSON didn't match the required schema. Fix these fields "
            "and respond again with ONLY the corrected JSON:\n  - "
            + "\n  - ".join(problems)
        )
        return None, coaching
    out = model.model_dump()
    # Preserve unknown keys the LLM volunteered — useful metadata, doesn't
    # need to be in the schema. Skip private keys (start with _).
    for k, v in raw.items():
        if k not in out and not k.startswith("_"):
            out[k] = v
    return out, None


def _dispatch_skill(skill_id: str, args: dict, ctx: SkillContext) -> dict:
    """Run a skill and return a dict that ALWAYS carries an `_error_class`
    field when something went wrong. The agent loop uses that field to
    decide whether to coach the LLM (LLM's fault: bad args) or to record
    a SKILL_BUG and consider auto-disabling the tool (our fault: skill
    code threw)."""
    from .error_classifier import classify_skill_error, ErrorClass

    skill = SKILL_REGISTRY.get(skill_id)
    if skill is None:
        # Treat unknown-skill as a config bug, not an LLM mistake — the
        # LLM was advertised a tool that doesn't exist. Surface loudly.
        return {"error": f"unknown skill {skill_id}",
                "_error_class": ErrorClass.PROVIDER_CONFIG.value}
    try:
        result = skill.handler(ctx, **args)
        # Normalize: some skills return non-dicts (e.g. a float).
        if not isinstance(result, dict):
            return {"value": result}
        return result
    except Exception as e:
        cls = classify_skill_error(e)
        reliability.record_error(
            "agent.dispatch_skill", e,
            context={"skill_id": skill_id, "args": args,
                     "error_class": cls.value},
            kind=cls.value,
        )
        if cls == ErrorClass.LLM_TOOL_MISUSE:
            # Coaching opportunity — surface a precise correction. Each
            # skill can supply an `error_hint` with the EXACT calling
            # convention (arg names, formats) so the LLM doesn't guess.
            # Falls back to the generic schema reminder if the skill
            # author hasn't written a hint yet.
            hint = (getattr(skill, "error_hint", "") or "").strip()
            if not hint:
                hint = ("Re-check the tool schema and pass the correct "
                        "argument names and types.")
            return {"error": f"bad args for {skill_id}: {e}",
                    "_error_class": cls.value,
                    "_coaching": hint}
        # SKILL_BUG or other: NOT the LLM's fault. Tell the LLM the tool
        # is unavailable so it routes around it instead of trying to
        # 'fix' a broken implementation. Auto-disable logic in the agent
        # loop will remove the tool entirely after a threshold.
        return {"error": f"tool {skill_id} is currently unavailable",
                "_error_class": cls.value,
                "_internal_message": str(e)}


# ---------- The agent loop ----------

def _should_reflect(decision: dict, tool_calls_made: list[dict],
                    enabled_skill_ids: set[str],
                    confidence_threshold: float = REFLECTION_CONFIDENCE_THRESHOLD
                    ) -> tuple[bool, str]:
    """Decide whether the agent's first decision warrants a re-plan pass.

    Returns (do_reflect, nudge_message). The nudge is appended as a user
    message so the agent re-enters its tool loop with concrete guidance
    about *what* it might have missed.
    """
    d = decision.get("decision")
    conf = float(decision.get("confidence") or 0)
    tools_used = {tc.get("name") for tc in tool_calls_made}

    # Soft / discrepancy without calling fuzzy_compare — we're asserting
    # similarity without checking it. Common LLM shortcut.
    if d in ("soft", "discrepancy") and "fuzzy_compare" in enabled_skill_ids \
            and "fuzzy_compare" not in tools_used:
        return True, (
            "You decided '" + d + "' without calling fuzzy_compare to verify "
            "name/reference similarity. Run fuzzy_compare on (proof.payer, "
            "txn.description) and (proof.reference, txn.reference|description), "
            "then re-decide. If your prior answer still holds, return the same "
            "JSON; otherwise update it."
        )

    # Strict without any FX call. Likely hallucinated rate.
    if d == "strict" and "get_fx_rate" in enabled_skill_ids \
            and "get_fx_rate" not in tools_used:
        return True, (
            "You returned 'strict' without calling get_fx_rate. Treasury "
            "Oracle policy: strict matches must be grounded in a live FX "
            "lookup. Call get_fx_rate(from_ccy, to_ccy, proof.date), then "
            "re-evaluate whether the converted amount really lines up."
        )

    # Discrepancy where SWIFT routing could explain the gap — try it.
    if d == "discrepancy" and "trace_swift_route" in enabled_skill_ids \
            and "trace_swift_route" not in tools_used:
        return True, (
            "You flagged this as a discrepancy but didn't call "
            "trace_swift_route. Call it now — if the gap is consistent with "
            "1–2 correspondent banks' standard fees, it may not be a "
            "discrepancy at all. Update your decision accordingly."
        )

    # Low confidence on ANY decision — invite recall_facts to surface
    # previously-learned patterns about this payer / currency pair.
    if conf < confidence_threshold and \
            "recall_facts" in enabled_skill_ids and \
            "recall_facts" not in tools_used:
        return True, (
            f"Your confidence ({conf:.2f}) is below the {confidence_threshold} "
            "reflection threshold. Call recall_facts on the payer name and "
            "the currency pair to surface anything the platform has learned "
            "from previous runs, then re-decide."
        )

    return False, ""


def _run_one_proof(proof: dict, candidates: list[dict], bank: str,
                   session_id: str, proof_idx: int,
                   platform_cfg: dict, tool_skills: list,
                   temperature: float = DEFAULT_AGENT_TEMPERATURE,
                   knobs: dict | None = None) -> dict:
    knobs = knobs or _resolve_knobs(platform_cfg)
    max_steps = int(knobs["max_steps"])
    reflection_threshold = float(knobs["reflection_confidence_threshold"])
    reflection_max = int(knobs["reflection_max_cycles"])
    system_prompt = _compose_system_prompt(tool_skills, platform_cfg)
    # Tool specs are recomputed each step from the current disabled-skill
    # set — if another proof in this parallel batch tripped auto-disable
    # on a skill, this proof's NEXT step will also stop offering it.

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": _build_user_prompt(proof, candidates, bank)},
    ]

    db.append_trace(session_id, proof.get("source_file"), 0, "user_prompt",
                    {"proof_index": proof_idx, "candidate_count": len(candidates),
                     "active_skills": [s.id for s in tool_skills]})

    # Build the LLM client ONCE per proof — test fixtures return a stub
    # whose internal state advances across calls (script[0], script[1], …).
    # Re-creating the client each step would reset that state and replay
    # the first response forever.
    _stub_client = get_client(False) if get_client is not chutes_client.get_client else None

    # Adaptive max_tokens — starts at the comfortable default, ratchets up
    # if a step's reasoning_content used most of the budget. Reasoning
    # models on hard proofs can spend 2500+ tokens just thinking; without
    # adapting we'd truncate the final JSON and force a parse_error nudge.
    AGENT_MAX_TOKENS_DEFAULT = 3000
    AGENT_MAX_TOKENS_CEILING = 5000
    REASONING_HEAVY_THRESHOLD = 2000
    next_max_tokens = AGENT_MAX_TOKENS_DEFAULT

    # Trigger-based pruning — resolve once per proof (bank currency lookup
    # is cheap but not free, and the candidate list is fixed for the run).
    bank_ccy = _bank_currency(bank)
    pruned_skills: list[tuple[str, str]] = []   # (skill_id, reason)
    relevant_skills: list = []
    for s in tool_skills:
        ok, why = _skill_applies(s, proof, candidates, bank_ccy)
        if ok:
            relevant_skills.append(s)
        else:
            pruned_skills.append((s.id, why))
    if pruned_skills:
        db.append_trace(session_id, proof.get("source_file"), 0, "skills_pruned", {
            "kept": [s.id for s in relevant_skills],
            "pruned": [{"skill_id": sid, "reason": why}
                       for sid, why in pruned_skills],
        })

    step = 0
    tool_call_log: list[dict] = []
    reflection_cycles = 0
    reflection_history: list[dict] = []  # each entry: {step, trigger, prior_decision}
    enabled_skill_ids = {s.id for s in tool_skills}

    for _ in range(max_steps):
        step += 1
        t0 = time.perf_counter()
        # Filter twice: (1) drop auto-disabled skills [persistent across
        # sessions per tenant], (2) keep only trigger-applicable ones for
        # THIS proof. Both filters narrow the tool spec section of the
        # prompt; smaller prompt = less reasoning-token waste.
        disabled = _disabled_skills_for(session_id)
        active_skills = [s for s in relevant_skills if s.id not in disabled]
        tool_specs = [s.to_openai_tool() for s in active_skills]
        # Step-1 policy: force the model to use a tool instead of letting
        # it deliberate ("should I call something or answer directly?").
        # Our prompt explicitly tells it to start with recall_facts — but
        # without `tool_choice="required"` reasoning models often just
        # emit a JSON decision based on priors and we lose the grounding.
        # On step 2+ we drop back to "auto" so it can return the final JSON.
        if step == 1 and tool_specs:
            step_tool_choice = "required"
        elif tool_specs:
            step_tool_choice = "auto"
        else:
            step_tool_choice = "none"
        try:
            # Route through llm_router so a failing/slow provider trips its
            # breaker and the next call automatically falls over to the
            # fallback key (or OpenAI/Anthropic if configured). The router
            # already wraps each provider in reliability.with_retry — don't
            # double-wrap, or one slow call eats 30s × 3 × N providers.
            with _agent_llm_semaphore:
                if _stub_client is not None:
                    # Test path — reuse the same stub client so its scripted
                    # state advances across steps.
                    resp = _stub_client.chat.completions.create(
                        model=REASONING_MODEL, messages=messages,
                        tools=tool_specs,
                        tool_choice=step_tool_choice,
                        temperature=temperature,
                        max_tokens=next_max_tokens, timeout=60,
                    )
                else:
                    resp = chutes_client.chat(
                        messages=messages, model=REASONING_MODEL,
                        tools=tool_specs,
                        tool_choice=step_tool_choice,
                        temperature=temperature,
                        # max_tokens is adaptive — starts at 3000, bumps
                        # to 5000 if the previous step's reasoning_content
                        # ate >2000 tokens (heavy-reasoning proof).
                        max_tokens=next_max_tokens, timeout=60,
                    )
            # Record which provider actually answered this step. llm_router
            # always populates last_provider() now that the agent goes through
            # it, so the old "guess chutes_primary" fallback is dead code.
            prov = llm_router.last_provider() or {}
            db.append_trace(session_id, proof.get("source_file"), step,
                            "provider", {
                                "provider": prov.get("provider", "unknown"),
                                "model": prov.get("model", REASONING_MODEL),
                                "fallback_from": prov.get("fallback_from", []),
                            })
        except Exception as e:
            latency = (time.perf_counter() - t0) * 1000
            db.append_trace(session_id, proof.get("source_file"), step, "error",
                            {"message": str(e)})
            db.record_metric(session_id, proof_index=proof_idx, step=step,
                             latency_ms=latency, status="error")
            reliability.record_error(
                "agent.run_one_proof", e,
                context={"session_id": session_id, "proof_index": proof_idx,
                         "step": step},
            )
            return {"decision": "error", "error": str(e), "fallback": True,
                    "tool_calls": tool_call_log}
        latency = (time.perf_counter() - t0) * 1000
        usage = getattr(resp, "usage", None)
        tokens_in = getattr(usage, "prompt_tokens", 0) or 0
        tokens_out = getattr(usage, "completion_tokens", 0) or 0
        details = getattr(usage, "prompt_tokens_details", None)
        tokens_cached = getattr(details, "cached_tokens", 0) or 0 if details else 0
        # Chutes reasoning models report `reasoning_tokens` either at the
        # top level of `usage` or under `completion_tokens_details`. Check
        # both. If reasoning was heavy this step, bump next step's budget
        # so the final-JSON has room.
        reasoning_tokens = getattr(usage, "reasoning_tokens", 0) or 0
        if not reasoning_tokens:
            comp_details = getattr(usage, "completion_tokens_details", None)
            reasoning_tokens = getattr(comp_details, "reasoning_tokens", 0) or 0 if comp_details else 0
        if reasoning_tokens >= REASONING_HEAVY_THRESHOLD and next_max_tokens < AGENT_MAX_TOKENS_CEILING:
            new_budget = min(AGENT_MAX_TOKENS_CEILING, next_max_tokens + 1500)
            db.append_trace(session_id, proof.get("source_file"), step,
                            "max_tokens_bumped", {
                                "reasoning_tokens": reasoning_tokens,
                                "from": next_max_tokens, "to": new_budget,
                            })
            next_max_tokens = new_budget
        db.record_metric(session_id, proof_index=proof_idx, step=step,
                         tokens_in=tokens_in, tokens_out=tokens_out,
                         tokens_cached=tokens_cached, latency_ms=latency,
                         status="ok")

        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []

        if tool_calls:
            messages.append({
                "role": "assistant", "content": msg.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name,
                                  "arguments": tc.function.arguments}}
                    for tc in tool_calls
                ],
            })
            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                db.append_trace(session_id, proof.get("source_file"), step,
                                "tool_call", {"name": tc.function.name, "arguments": args})

                skill = SKILL_REGISTRY.get(tc.function.name)
                skill_cfg = resolve_skill_config(skill, platform_cfg) if skill else {}
                ctx = SkillContext(
                    session_id=session_id,
                    config=platform_cfg,
                    skill_config=skill_cfg,
                    extras={"proof": proof, "candidates": candidates, "bank": bank},
                )
                tt0 = time.perf_counter()
                result = _dispatch_skill(tc.function.name, args, ctx)
                tlat = (time.perf_counter() - tt0) * 1000
                db.record_metric(
                    session_id, proof_index=proof_idx, step=step,
                    skill_id=tc.function.name, latency_ms=tlat,
                    status=("error" if isinstance(result, dict) and result.get("error") else "ok"),
                )

                tool_call_log.append(
                    {"name": tc.function.name, "arguments": args, "result": result}
                )
                db.append_trace(session_id, proof.get("source_file"), step,
                                "tool_result", {"name": tc.function.name, "result": result})

                # Auto-disable check — if the skill threw an internal bug
                # (not a bad-args coaching opportunity), bump the per-session
                # failure counter. When it crosses the threshold, drop the
                # skill from this session's tool spec on subsequent steps
                # so the LLM stops wasting reasoning tokens trying to use it.
                err_class = result.get("_error_class") if isinstance(result, dict) else None
                # Success path → reset the per-tenant failure counter for
                # this skill (sliding-window recovery). LLM_TOOL_MISUSE
                # doesn't count as success but also doesn't count as a
                # skill bug — it's the LLM's fault, so leave the counter.
                if err_class is None:
                    try:
                        db.record_skill_success(tc.function.name)
                    except Exception:
                        pass  # never let bookkeeping break the agent
                if err_class == "skill_bug":
                    err_msg = (result.get("_internal_message") or
                               result.get("error") or "")
                    n_fail = _record_skill_failure(
                        session_id, tc.function.name, error=err_msg,
                    )
                    if n_fail >= _SKILL_AUTODISABLE_THRESHOLD:
                        db.append_trace(session_id, proof.get("source_file"), step,
                                        "skill_autodisabled", {
                                            "skill_id": tc.function.name,
                                            "failures": n_fail,
                                            "threshold": _SKILL_AUTODISABLE_THRESHOLD,
                                            "reason": "internal skill error (not LLM fault)",
                                        })

                messages.append({
                    "role": "tool", "tool_call_id": tc.id,
                    "content": db.safe_dumps(result),
                })
            continue

        # Use extract_content (not msg.content directly) so reasoning models
        # that emit their final JSON inside `reasoning_content` instead of
        # `content` still get parsed. The salvager finds the last balanced
        # {...} block, which is where reasoning models tend to put the answer.
        from .chutes_client import extract_content as _extract_content
        final_text = _extract_content(resp)
        final_raw = _extract_final_json(final_text)
        schema_nudge = None
        if final_raw is not None:
            # Pydantic-validate + normalize the decision shape. If the LLM
            # returned the right keys with wrong case / wrong types, this
            # fixes them silently. If the shape is genuinely wrong, we get
            # a coaching message naming the bad fields.
            final, schema_nudge = _validate_decision(final_raw)
        else:
            final = None
        if final is not None:
            db.append_trace(session_id, proof.get("source_file"), step,
                            "decision", final)
            # Reflection — give the agent one shot to reconsider when its
            # initial decision shows red flags (low confidence, missing
            # critical tool calls). Bounded by REFLECTION_MAX_CYCLES so we
            # can't burn the whole step budget on second-guessing.
            if reflection_cycles < reflection_max:
                should, nudge = _should_reflect(final, tool_call_log,
                                                enabled_skill_ids,
                                                confidence_threshold=reflection_threshold)
                if should:
                    reflection_cycles += 1
                    reflection_history.append({
                        "step": step,
                        "trigger": nudge[:120],
                        "prior_decision": final.get("decision"),
                        "prior_confidence": final.get("confidence"),
                    })
                    db.append_trace(session_id, proof.get("source_file"), step,
                                    "reflection", {
                                        "cycle": reflection_cycles,
                                        "prior_decision": final.get("decision"),
                                        "prior_confidence": final.get("confidence"),
                                        "nudge": nudge[:200],
                                    })
                    # Inject the assistant's prior answer back into history so
                    # the LLM sees its own claim, then nudge as a user message.
                    messages.append({"role": "assistant",
                                     "content": json.dumps(final)})
                    messages.append({"role": "user", "content": nudge})
                    continue
            final["tool_calls"] = tool_call_log
            if reflection_history:
                final["reflection_history"] = reflection_history
            return final
        # Differentiate the failure mode so the coaching message is honest:
        # - empty `content` (reasoning ate the budget)
        # - parseable JSON but failed schema validation (wrong field types)
        # - unparseable JSON
        # Misdiagnosing this is what used to lock the agent in a loop.
        content_was_empty = not (msg.content or "").strip()
        db.append_trace(session_id, proof.get("source_file"), step, "parse_error",
                        {"raw": (final_text or msg.content or "")[:500],
                         "content_empty": content_was_empty,
                         "schema_failure": bool(schema_nudge)})
        if schema_nudge:
            # JSON parsed fine but a field had wrong type / value. The
            # schema_nudge lists exactly which fields and why — far more
            # useful than a generic "respond with valid JSON" reminder.
            nudge_msg = schema_nudge
        elif content_was_empty:
            nudge_msg = ("Your previous response was empty in `content` — the "
                         "model spent its entire token budget on reasoning. "
                         "Be MUCH more concise in your next response: skip "
                         "explanatory text and emit ONLY the JSON object.")
        else:
            nudge_msg = ("Your previous response wasn't valid JSON. Return ONLY the "
                         "JSON object specified in the system prompt — no commentary, "
                         "no code fences.")
        messages.append({"role": "user", "content": nudge_msg})

    db.append_trace(session_id, proof.get("source_file"), step, "exhausted",
                    {"max_steps": max_steps})
    return {"decision": "no_match", "confidence": 0.0, "fallback": True,
            "reasoning": "Agent exhausted step budget without deciding.",
            "tool_calls": tool_call_log}


# ---------- Public entry point ----------

def reconcile_agent(proofs: list[dict], txns: list[dict], bank: str,
                    session_id: str | None = None,
                    config_override: dict | None = None,
                    temperature: float | None = None) -> dict:
    """Run the agentic reconciliation across all proofs and persist the session.

    If `config_override` is provided it replaces the loaded platform config for
    this run only (useful for tests / per-tenant calls).

    `temperature` overrides the LLM sampling temperature. Pass 0.0 for
    deterministic eval runs. If None, the platform config's
    `agent_temperature` is used, falling back to DEFAULT_AGENT_TEMPERATURE.
    """
    sid = session_id or str(uuid.uuid4())[:8]
    txns = [t for t in txns if t.get("direction", "in") == "in"]
    platform_cfg = config_override or platform_config.load_config()
    tool_skills = enabled_tool_skills(platform_cfg)
    if not tool_skills:
        # Defensive: surface this clearly rather than silently degrading.
        return {"matches": [], "soft_matches": [], "unmatched_proofs": proofs,
                "unmatched_txns": txns, "trace": ["No tool skills enabled."],
                "summary": {"total_proofs": len(proofs), "total_txns": len(txns),
                            "matched": 0, "soft_matches": 0,
                            "unmatched_proofs": len(proofs),
                            "unmatched_txns": len(txns)},
                "mode": "agent", "error": "no_tool_skills_enabled"}

    # Resolve all per-tenant knobs once, with per-bank tolerance override.
    knobs = _resolve_knobs(platform_cfg, bank=bank)

    # Effective temperature: explicit arg > config knob > default.
    if temperature is None:
        temperature = float(knobs.get("agent_temperature") or DEFAULT_AGENT_TEMPERATURE)

    used_ids: set[str] = set()
    matches: list[dict] = []
    soft_matches: list[dict] = []
    unmatched_proofs: list[dict] = []
    classical_trace: list[str] = []

    # ---- Phase 1: cheap pre-filter (no LLM) ----
    # Bucket proofs into "skip" (unreadable / low-OCR / no candidates) and
    # "to_run" (need the LLM). For to_run we compute candidates *without* the
    # used_ids filter — used_ids is resolved sequentially in phase 3 after
    # all LLM calls return, so parallel proofs see a stable candidate set.
    to_run: list[tuple[int, dict, list[dict]]] = []  # (proof_idx, proof, candidates)
    pre_skipped: dict[int, dict] = {}  # proof_idx -> classical_trace_line (decision handled here)
    for pi, proof in enumerate(proofs):
        src = proof.get("source_file", f"proof_{pi}")
        if proof.get("error") or not proof.get("amount") or not proof.get("currency"):
            unmatched_proofs.append({**proof, "reason": "Proof could not be parsed"})
            pre_skipped[pi] = {"trace": f"SKIP {src} — unreadable"}
            continue
        q = proof.get("ocr_quality") or {}
        if q.get("gate") == "low_quality":
            unmatched_proofs.append({
                **proof,
                "reason": (f"OCR quality below gate ({q.get('completeness')}); "
                           f"missing: {', '.join(q.get('missing_fields') or [])}. "
                           f"Needs human review."),
                "needs_review": True,
            })
            pre_skipped[pi] = {"trace": (
                f"SKIP {src} — ocr_quality={q.get('completeness')} "
                f"(missing {q.get('missing_fields')})"
            )}
            continue
        candidates = _candidate_txns(proof, txns, set())  # no used_ids yet — parallel safe
        if not candidates:
            unmatched_proofs.append({**proof, "reason": "No bank transactions within date window"})
            pre_skipped[pi] = {"trace": f"PROOF {src}: no candidates"}
            continue
        to_run.append((pi, proof, candidates))

    # Emit pre-filter trace lines in proof order (deterministic output).
    for pi in range(len(proofs)):
        if pi in pre_skipped:
            classical_trace.append(pre_skipped[pi]["trace"])
        else:
            # Find this proof's candidate count for the trace.
            for tpi, tproof, tcands in to_run:
                if tpi == pi:
                    classical_trace.append(
                        f"PROOF {tproof.get('source_file', f'proof_{pi}')}: "
                        f"{tproof['amount']} {tproof['currency']} on "
                        f"{tproof.get('date','?')} ({len(tcands)} candidates) — "
                        f"agent thinking…"
                    )
                    break

    # ---- Phase 2: parallel LLM runs ----
    # ThreadPoolExecutor over the LLM-bound work. The per-call semaphore
    # (`_agent_llm_semaphore`, cap=AGENT_LLM_CONCURRENCY) inside _run_one_proof
    # caps how many in-flight LLM requests we actually issue, so a larger
    # worker pool is fine — workers just wait on the semaphore.
    decisions: dict[int, dict] = {}
    if to_run:
        max_workers = max(2, min(len(to_run), AGENT_LLM_CONCURRENCY * 2))
        with ThreadPoolExecutor(max_workers=max_workers,
                                thread_name_prefix="agent") as pool:
            futures = {
                pool.submit(_run_one_proof, proof, candidates, bank, sid, pi,
                            platform_cfg, tool_skills,
                            temperature=temperature, knobs=knobs): (pi, proof, candidates)
                for pi, proof, candidates in to_run
            }
            for fut in as_completed(futures):
                pi, proof, candidates = futures[fut]
                try:
                    decisions[pi] = fut.result()
                except Exception as e:
                    reliability.record_error(
                        "agent.reconcile_agent.parallel", e,
                        context={"session_id": sid, "proof_index": pi},
                    )
                    decisions[pi] = {"decision": "error", "error": str(e),
                                     "fallback": True, "tool_calls": []}

    # ---- Phase 3: sequential post-processing (in proof order) ----
    # used_ids is resolved here. If two proofs picked the same txn the
    # later-indexed proof loses it and falls through to no_match. This
    # preserves the original semantics (earlier proofs win) while letting
    # all LLM calls run concurrently.
    for pi, proof in enumerate(proofs):
        if pi in pre_skipped:
            continue
        decision = decisions.get(pi)
        if decision is None:
            continue
        # Recompute candidate list for chosen-index lookup — same args as phase 1.
        candidates = _candidate_txns(proof, txns, set())

        d = decision.get("decision")
        ti = decision.get("txn_index")
        chosen = candidates[ti] if isinstance(ti, int) and 0 <= ti < len(candidates) else None

        # Guardrail: if the agent based a strict decision on an untrusted FX
        # source (static fallback or identity), downgrade to soft so the
        # operator confirms. This catches both the case where the LLM ignored
        # the trusted=false flag and the case where it never asked for a rate.
        fx_results = [
            tc.get("result", {}) for tc in (decision.get("tool_calls") or [])
            if tc.get("name") == "get_fx_rate" and isinstance(tc.get("result"), dict)
        ]
        untrusted_fx = [r for r in fx_results if r.get("trusted") is False]
        if d == "strict" and untrusted_fx:
            classical_trace.append(
                f"  [!] downgraded strict→soft: FX source "
                f"{untrusted_fx[0].get('source')} not trusted for strict match"
            )
            d = "soft"
            decision["decision"] = "soft"
            decision.setdefault("fuzzy_signals", []).append(
                f"fx_source={untrusted_fx[0].get('source')}"
            )

        # F2: second-pass verifier ensemble.
        # Pass 1 — deterministic skeptic (5 rules, free).
        det_verifier = _verify_decision(decision, proof, chosen, bank, knobs=knobs)
        # Pass 2 — LLM auditor (independent prompt, separate model call).
        llm_enabled = bool(knobs.get("verifier_llm_enabled", True))
        if llm_enabled and d == "strict" and chosen is not None:
            llm_verifier = _verifier_mod.llm_verify(
                decision, proof, chosen, bank,
                model_profile=knobs.get("verifier_model_profile", "cheap"),
                temperature=0.0,
            )
        else:
            llm_verifier = {"ran": False, "verdict": "skip", "concerns": [],
                            "method": "llm_verifier_v1", "reasoning": ""}
        verifier = _verifier_mod.merge_verifiers(det_verifier, llm_verifier)
        if verifier["verdict"] == "downgrade":
            classical_trace.append(
                f"  [!] verifier downgraded strict→soft: "
                + "; ".join(verifier["concerns"])
            )
            d = "soft"
            decision["decision"] = "soft"
            decision.setdefault("fuzzy_signals", []).extend(
                f"verifier:{c.split(' — ')[0]}" for c in verifier["concerns"]
            )
            db.append_trace(sid, proof.get("source_file"), 0, "verifier_downgrade",
                            verifier)
        elif verifier["ran"]:
            db.append_trace(sid, proof.get("source_file"), 0, "verifier_confirm",
                            verifier)

        prov = _build_provenance(decision, proof, chosen, bank)
        prov["verifier"] = verifier

        # Parallel-mode conflict resolution — if an earlier proof already
        # claimed this txn, demote this one to no_match. Earlier proofs win
        # because phase 3 walks proofs in index order.
        if chosen and chosen["id"] in used_ids:
            unmatched_proofs.append({
                **proof,
                "reason": (f"Agent chose txn {chosen['id']} but it was already "
                           f"matched to an earlier proof."),
                "agent_tool_calls": decision.get("tool_calls", []),
            })
            classical_trace.append(
                f"  [X] AGENT CONFLICT — txn {chosen['id']} already taken"
            )
            continue

        if d == "strict" and chosen:
            raw_conf = float(decision.get("confidence") or 0.95)
            cal_conf = calibration.apply(raw_conf)
            used_ids.add(chosen["id"])
            matches.append({
                "proof": proof, "txn": chosen,
                "conversion": {
                    "fx_rate": decision.get("fx_rate") or 0,
                    "expected_gross": round((decision.get("fx_rate") or 0) * proof["amount"], 2),
                    "expected_net": decision.get("expected_net") or 0,
                    "actual_received": decision.get("actual") or chosen["amount"],
                    "fee_pct": (decision.get("fee_amount") or 0) /
                               max((decision.get("fx_rate") or 0) * proof["amount"], 1e-6),
                    "fee_amount": decision.get("fee_amount") or 0,
                    "provenance": prov,
                },
                "confidence": cal_conf,
                "confidence_raw": raw_conf,
                "reasoning": decision.get("reasoning") or "Agent matched.",
                "status": "matched",
                "agent_tool_calls": decision.get("tool_calls", []),
            })
            classical_trace.append(f"  [OK] AGENT MATCHED → {chosen['id']}")
        elif d == "soft" and chosen:
            raw_conf = float(decision.get("confidence") or 0.6)
            cal_conf = calibration.apply(raw_conf)
            soft_matches.append({
                "proof": proof, "txn": chosen,
                "conversion": {
                    "fx_rate": decision.get("fx_rate") or 0,
                    "expected_net": decision.get("expected_net") or 0,
                    "actual_received": decision.get("actual") or chosen["amount"],
                    "provenance": prov,
                },
                "confidence": cal_conf,
                "confidence_raw": raw_conf,
                "signals": decision.get("fuzzy_signals") or [],
                "reasoning": decision.get("reasoning") or "Soft match.",
                "status": "soft_match_pending",
                "agent_tool_calls": decision.get("tool_calls", []),
            })
            classical_trace.append(f"  [?] AGENT SOFT MATCH → {chosen['id']}")
        elif d == "discrepancy":
            unmatched_proofs.append({
                **proof,
                "reason": decision.get("reasoning") or "Agent flagged as discrepancy",
                "closest_txn": chosen,
                "expected_net": decision.get("expected_net"),
                "actual": decision.get("actual"),
                "fx_rate": decision.get("fx_rate"),
                "swift_route": decision.get("swift_route"),
                "agent_tool_calls": decision.get("tool_calls", []),
            })
            classical_trace.append(f"  [X] AGENT DISCREPANCY")
        else:
            unmatched_proofs.append({
                **proof, "reason": decision.get("reasoning") or "Agent: no match",
                "agent_tool_calls": decision.get("tool_calls", []),
            })
            classical_trace.append(f"  [X] AGENT NO MATCH ({d})")

    unmatched_txns = [t for t in txns if t["id"] not in used_ids
                      and not any(s["txn"]["id"] == t["id"] for s in soft_matches)]

    # Snapshot auto-disable state for this session, then clear the bucket
    # so the in-memory map doesn't grow forever.
    disabled_final = sorted(_disabled_skills_for(sid))
    _clear_session_skill_failures(sid)

    result = {
        "matches": matches,
        "soft_matches": soft_matches,
        "unmatched_proofs": unmatched_proofs,
        "unmatched_txns": unmatched_txns,
        "trace": classical_trace,
        "summary": {
            "total_proofs": len(proofs),
            "total_txns": len(txns),
            "matched": len(matches),
            "soft_matches": len(soft_matches),
            "unmatched_proofs": len(unmatched_proofs),
            "unmatched_txns": len(unmatched_txns),
        },
        "agent_trace": db.get_trace(sid),
        "mode": "agent",
        "active_skills": [s.id for s in tool_skills],
        "auto_disabled_skills": disabled_final,
        "prompt_versions": platform_config.active_prompt_versions(platform_cfg),
    }
    db.save_session(sid, bank, result)
    # Attach final telemetry after save so the response carries it back.
    result["metrics"] = db.session_metrics(sid)
    result["recon_id"] = sid
    return result
