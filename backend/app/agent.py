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
from datetime import datetime
from typing import Any

from .chutes_client import get_client
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

# Verifier (F2) — second-pass skeptic that audits the agent's proposed decision.
# Currently deterministic (free, zero extra tokens); an LLM-backed verifier can
# be plugged into the same hook later.
VERIFIER_STRICT_DIFF_PCT = 0.005        # >0.5% diff is unusual for a 'strict' claim
VERIFIER_STRICT_DAYS_OFF = 2            # date gap that should be a 'soft' not 'strict'
VERIFIER_MIN_TOOL_CALLS_FOR_STRICT = 1  # an LLM jumping straight to strict is suspect


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
                     bank: str) -> dict:
    """Deterministic skeptic pass. Returns:

        {
          "ran": True,
          "verdict": "confirm" | "downgrade" | "skip",
          "concerns": [str, ...],
          "method": "programmatic_skeptic_v1"
        }

    For non-strict decisions it returns verdict='skip' (nothing to audit).
    For strict decisions: any concern triggers verdict='downgrade'.
    """
    d = decision.get("decision")
    if d != "strict" or chosen is None:
        return {"ran": False, "verdict": "skip", "concerns": [],
                "method": "programmatic_skeptic_v1"}

    concerns: list[str] = []

    # 1) Diff% larger than verifier's stricter ceiling — agent's 2% match
    # tolerance may be config, but for a STRICT claim we want a much tighter fit.
    expected = float(decision.get("expected_net") or 0)
    actual = float(decision.get("actual") or chosen.get("amount") or 0)
    if expected > 0:
        diff_pct = abs(actual - expected) / expected
        if diff_pct > VERIFIER_STRICT_DIFF_PCT:
            concerns.append(
                f"strict claimed but diff is {diff_pct*100:.2f}% — above "
                f"verifier ceiling {VERIFIER_STRICT_DIFF_PCT*100:.2f}%"
            )

    # 2) Date gap — same-day payments are most likely strict; multi-day gaps
    # commonly mean a fee/FX adjustment landed late, not a clean match.
    try:
        from datetime import date as _d
        pd_ = _d.fromisoformat(proof.get("date"))
        td_ = _d.fromisoformat(chosen.get("date"))
        days_off = abs((pd_ - td_).days)
        if days_off > VERIFIER_STRICT_DAYS_OFF:
            concerns.append(
                f"strict claimed but proof and txn are {days_off} days apart "
                f"(verifier wants ≤{VERIFIER_STRICT_DAYS_OFF})"
            )
    except Exception:
        pass

    # 3) No supporting tool calls — LLM jumped straight to strict without
    # actually calling get_fx_rate / fuzzy_compare. Hallucination risk.
    tool_calls = decision.get("tool_calls") or []
    n_substantive = sum(
        1 for tc in tool_calls
        if tc.get("name") in {"get_fx_rate", "apply_bank_fee", "fuzzy_compare"}
    )
    if n_substantive < VERIFIER_MIN_TOOL_CALLS_FOR_STRICT:
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
    """Merge base prompt with per-skill usage guidance."""
    sections = [_BASE_PROMPT, "", "Tool usage guidance:"]
    for s in tool_skills:
        sc = resolve_skill_config(s, platform_cfg)
        sections.append(f"- {s.id}: {sc.get('system_prompt', '').strip()}")
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
    if not content:
        return None
    txt = content.strip()
    if txt.startswith("```"):
        parts = txt.split("```")
        if len(parts) >= 2:
            inner = parts[1]
            if inner.startswith("json"):
                inner = inner[4:]
            txt = inner.strip()
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        return None


def _dispatch_skill(skill_id: str, args: dict, ctx: SkillContext) -> Any:
    skill = SKILL_REGISTRY.get(skill_id)
    if skill is None:
        return {"error": f"unknown skill {skill_id}"}
    try:
        return skill.handler(ctx, **args)
    except TypeError as e:
        reliability.record_error(
            "agent.dispatch_skill", e,
            context={"skill_id": skill_id, "args": args, "reason": "bad_args"},
        )
        return {"error": f"bad args for {skill_id}: {e}"}
    except Exception as e:
        reliability.record_error(
            "agent.dispatch_skill", e,
            context={"skill_id": skill_id, "args": args},
        )
        return {"error": str(e)}


# ---------- The agent loop ----------

def _run_one_proof(proof: dict, candidates: list[dict], bank: str,
                   session_id: str, proof_idx: int,
                   platform_cfg: dict, tool_skills: list,
                   temperature: float = DEFAULT_AGENT_TEMPERATURE) -> dict:
    system_prompt = _compose_system_prompt(tool_skills, platform_cfg)
    tool_specs = [s.to_openai_tool() for s in tool_skills]

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": _build_user_prompt(proof, candidates, bank)},
    ]

    db.append_trace(session_id, proof.get("source_file"), 0, "user_prompt",
                    {"proof_index": proof_idx, "candidate_count": len(candidates),
                     "active_skills": [s.id for s in tool_skills]})

    client = get_client(False)
    step = 0
    tool_call_log: list[dict] = []

    for _ in range(MAX_STEPS):
        step += 1
        t0 = time.perf_counter()
        try:
            resp = reliability.with_retry(
                lambda: client.chat.completions.create(
                    model=REASONING_MODEL,
                    messages=messages,
                    tools=tool_specs,
                    tool_choice="auto",
                    temperature=temperature,
                    max_tokens=900,
                    timeout=30,
                ),
                source="agent.run_one_proof",
            )
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
                messages.append({
                    "role": "tool", "tool_call_id": tc.id,
                    "content": db.safe_dumps(result),
                })
            continue

        final = _extract_final_json(msg.content)
        if final is not None:
            db.append_trace(session_id, proof.get("source_file"), step,
                            "decision", final)
            final["tool_calls"] = tool_call_log
            return final
        db.append_trace(session_id, proof.get("source_file"), step, "parse_error",
                        {"raw": (msg.content or "")[:500]})
        messages.append({
            "role": "user",
            "content": "Your previous response wasn't valid JSON. Return ONLY the "
                       "JSON object specified in the system prompt — no commentary, "
                       "no code fences.",
        })

    db.append_trace(session_id, proof.get("source_file"), step, "exhausted",
                    {"max_steps": MAX_STEPS})
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
    # Inbound payments only — outbound rows (refunds, fees) should never
    # match against a payment proof. Rows lacking direction (legacy callers)
    # are assumed inbound to preserve back-compat.
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

    # Resolve effective temperature: explicit arg > config override > default.
    if temperature is None:
        cfg_temp = platform_cfg.get("agent_temperature")
        temperature = (float(cfg_temp) if cfg_temp is not None
                       else DEFAULT_AGENT_TEMPERATURE)

    used_ids: set[str] = set()
    matches: list[dict] = []
    soft_matches: list[dict] = []
    unmatched_proofs: list[dict] = []
    classical_trace: list[str] = []

    for pi, proof in enumerate(proofs):
        src = proof.get("source_file", f"proof_{pi}")
        if proof.get("error") or not proof.get("amount") or not proof.get("currency"):
            unmatched_proofs.append({**proof, "reason": "Proof could not be parsed"})
            classical_trace.append(f"SKIP {src} — unreadable")
            continue

        # OCR quality gate: low-completeness proofs never reach the LLM agent.
        # The agent would spend tokens guessing missing fields and likely
        # produce a wrong-confident match. Far better to surface for review.
        q = proof.get("ocr_quality") or {}
        if q.get("gate") == "low_quality":
            unmatched_proofs.append({
                **proof,
                "reason": (f"OCR quality below gate ({q.get('completeness')}); "
                           f"missing: {', '.join(q.get('missing_fields') or [])}. "
                           f"Needs human review."),
                "needs_review": True,
            })
            classical_trace.append(
                f"SKIP {src} — ocr_quality={q.get('completeness')} "
                f"(missing {q.get('missing_fields')})"
            )
            continue

        candidates = _candidate_txns(proof, txns, used_ids)
        if not candidates:
            unmatched_proofs.append({**proof, "reason": "No bank transactions within date window"})
            classical_trace.append(f"PROOF {src}: no candidates")
            continue

        classical_trace.append(
            f"PROOF {src}: {proof['amount']} {proof['currency']} on {proof.get('date','?')} "
            f"({len(candidates)} candidates) — agent thinking…"
        )
        decision = _run_one_proof(proof, candidates, bank, sid, pi,
                                  platform_cfg, tool_skills,
                                  temperature=temperature)

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
        det_verifier = _verify_decision(decision, proof, chosen, bank)
        # Pass 2 — LLM auditor (independent prompt, separate model call).
        # Skipped when disabled by config, or when not a strict decision.
        llm_enabled = bool(platform_cfg.get("verifier_llm_enabled", True))
        if llm_enabled and d == "strict" and chosen is not None:
            llm_verifier = _verifier_mod.llm_verify(
                decision, proof, chosen, bank,
                model_profile=platform_cfg.get("verifier_model_profile", "cheap"),
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
        "prompt_versions": platform_config.active_prompt_versions(platform_cfg),
    }
    db.save_session(sid, bank, result)
    # Attach final telemetry after save so the response carries it back.
    result["metrics"] = db.session_metrics(sid)
    result["recon_id"] = sid
    return result
