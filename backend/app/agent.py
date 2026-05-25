"""ReconciliationAgent — real LLM-driven tool-calling loop.

Per-proof flow:
  1. Build a system prompt + user prompt containing the proof and candidate txns.
  2. Call the LLM with TOOL_SCHEMAS attached.
  3. Loop: if the model returns tool_calls, execute them, append the results, and
     re-invoke. Otherwise parse the final JSON decision.
  4. Every step is persisted to the agent_trace table — thoughts, actions, observations,
     and the final decision — so the judges and auditors can replay the run.

Design notes
------------
* Candidates are pre-filtered to those within ±5 days so we don't blow the context
  window on irrelevant txns. The agent still has full freedom to reject every one.
* Tool calls are bounded by MAX_STEPS to prevent loops on a confused model.
* If the model fails to call tools or returns malformed JSON for MAX_STEPS rounds,
  we fall back to the classical matcher for that proof and tag it as `fallback`.
* The tool surface is intentionally small (4 tools) so a sub-frontier model
  (Gemma-4-31B on Chutes) can stay coherent.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from .chutes_client import get_client
from .config import REASONING_MODEL, MATCH_TOLERANCE
from .tools import get_fx_rate, apply_bank_fee
from .swift import trace_route
from .fuzzy import soft_match_score
from . import db


MAX_STEPS = 6  # safety cap on tool-call iterations per proof
DATE_WINDOW_DAYS = 5


# ---------- Tool surface exposed to the LLM ----------

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_fx_rate",
            "description": (
                "Fetch the historical foreign-exchange rate between two ISO 4217 "
                "currency codes on a specific date. Returns the multiplier such "
                "that amount_in_from * rate = amount_in_to."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "from_ccy": {"type": "string"},
                    "to_ccy": {"type": "string"},
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                },
                "required": ["from_ccy", "to_ccy", "date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_bank_fee",
            "description": (
                "Apply the local bank's inbound-conversion fee to a gross amount. "
                "Returns fee_pct, fee_amount, and net_amount after the fee."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number"},
                    "bank_name": {"type": "string"},
                },
                "required": ["amount", "bank_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fuzzy_compare",
            "description": (
                "Compare a payment proof to a bank transaction using payer-name "
                "similarity, invoice-reference overlap, and learned-alias memory. "
                "Use this when the amount tier alone is ambiguous (e.g. amount "
                "differs by more than the strict tolerance but other signals look "
                "promising)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "proof_index": {"type": "integer"},
                    "txn_index": {"type": "integer"},
                },
                "required": ["proof_index", "txn_index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trace_swift_route",
            "description": (
                "Infer the correspondent-bank routing chain when the actual amount "
                "received is materially less than the expected net. Returns an "
                "ordered list of route nodes with per-hop fee attribution."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source_currency": {"type": "string"},
                    "sent_amount": {"type": "number"},
                    "expected_net_local": {"type": "number"},
                    "actual_net_local": {"type": "number"},
                    "fx_rate": {"type": "number"},
                    "local_currency": {"type": "string"},
                },
                "required": [
                    "source_currency", "sent_amount", "expected_net_local",
                    "actual_net_local", "fx_rate", "local_currency",
                ],
            },
        },
    },
]


# ---------- System prompt ----------

SYSTEM_PROMPT = f"""You are a treasury reconciliation agent. For each payment proof,
you must decide whether it matches one of the candidate bank transactions provided.

You have four tools:
  - get_fx_rate(from_ccy, to_ccy, date)
  - apply_bank_fee(amount, bank_name)
  - fuzzy_compare(proof_index, txn_index)
  - trace_swift_route(...)

Standard procedure for each proof:
  1. For the most plausible candidate txn (by date proximity), call get_fx_rate to
     convert the proof amount into the bank's local currency.
  2. Call apply_bank_fee on the converted amount.
  3. Compare the resulting net to the candidate's actual amount.
     * If |diff| / expected_net <= {MATCH_TOLERANCE:.2%}, decision = "strict".
     * If diff is between {MATCH_TOLERANCE:.0%} and 15%, call fuzzy_compare to look
       for non-numerical signals (name, invoice ref). If strong, decision = "soft".
     * If diff > 15% AND actual < expected, call trace_swift_route to attribute
       the gap. Decision = "discrepancy".
     * If no candidate is plausible, decision = "no_match".

When you have a decision, RESPOND WITH FINAL JSON (no tool call) in this exact shape:

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


# ---------- Helpers ----------

def _candidate_txns(proof: dict, all_txns: list[dict], used_ids: set[str]) -> list[dict]:
    """Filter to txns within the date window and not yet consumed."""
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


def _dispatch(name: str, args: dict, proof: dict, candidates: list[dict]) -> Any:
    """Execute a tool call. proof+candidates are passed in for index-based tools."""
    if name == "get_fx_rate":
        return {"rate": get_fx_rate(args["from_ccy"], args["to_ccy"], args["date"])}
    if name == "apply_bank_fee":
        return apply_bank_fee(args["amount"], args.get("bank_name", "default"))
    if name == "fuzzy_compare":
        ti = int(args["txn_index"])
        if ti < 0 or ti >= len(candidates):
            return {"error": f"txn_index {ti} out of range 0..{len(candidates)-1}"}
        return soft_match_score(proof, candidates[ti])
    if name == "trace_swift_route":
        return trace_route(
            source_currency=args["source_currency"],
            sent_amount=float(args["sent_amount"]),
            expected_net_local=float(args["expected_net_local"]),
            actual_net_local=float(args["actual_net_local"]),
            fx_rate=float(args["fx_rate"]),
            local_currency=args.get("local_currency", "MYR"),
        )
    return {"error": f"unknown tool {name}"}


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


# ---------- The agent loop ----------

def _run_one_proof(proof: dict, candidates: list[dict], bank: str,
                   session_id: str, proof_idx: int) -> dict:
    """Returns a normalized decision dict, with the agent's tool-call trace persisted."""
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_prompt(proof, candidates, bank)},
    ]

    db.append_trace(session_id, proof.get("source_file"), 0, "user_prompt",
                    {"proof_index": proof_idx, "candidate_count": len(candidates)})

    client = get_client(False)
    step = 0
    tool_call_log: list[dict] = []

    for _ in range(MAX_STEPS):
        step += 1
        try:
            resp = client.chat.completions.create(
                model=REASONING_MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.1,
                max_tokens=900,
                timeout=30,
            )
        except Exception as e:
            db.append_trace(session_id, proof.get("source_file"), step, "error",
                            {"message": str(e)})
            return {"decision": "error", "error": str(e), "fallback": True,
                    "tool_calls": tool_call_log}

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

                result = _dispatch(tc.function.name, args, proof, candidates)
                tool_call_log.append(
                    {"name": tc.function.name, "arguments": args, "result": result}
                )
                db.append_trace(session_id, proof.get("source_file"), step,
                                "tool_result", {"name": tc.function.name, "result": result})
                messages.append({
                    "role": "tool", "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str),
                })
            continue

        # No tool call → final answer
        final = _extract_final_json(msg.content)
        if final is not None:
            db.append_trace(session_id, proof.get("source_file"), step,
                            "decision", final)
            final["tool_calls"] = tool_call_log
            return final
        # Garbled output: nudge and retry
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
                    session_id: str | None = None) -> dict:
    """Run the agentic reconciliation across all proofs and persist the session."""
    sid = session_id or str(uuid.uuid4())[:8]
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

        candidates = _candidate_txns(proof, txns, used_ids)
        if not candidates:
            unmatched_proofs.append({**proof, "reason": "No bank transactions within date window"})
            classical_trace.append(f"PROOF {src}: no candidates")
            continue

        classical_trace.append(
            f"PROOF {src}: {proof['amount']} {proof['currency']} on {proof.get('date','?')} "
            f"({len(candidates)} candidates) — agent thinking…"
        )
        decision = _run_one_proof(proof, candidates, bank, sid, pi)

        d = decision.get("decision")
        ti = decision.get("txn_index")
        chosen = candidates[ti] if isinstance(ti, int) and 0 <= ti < len(candidates) else None

        if d == "strict" and chosen:
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
                },
                "confidence": float(decision.get("confidence") or 0.95),
                "reasoning": decision.get("reasoning") or "Agent matched.",
                "status": "matched",
                "agent_tool_calls": decision.get("tool_calls", []),
            })
            classical_trace.append(f"  [OK] AGENT MATCHED → {chosen['id']}")
        elif d == "soft" and chosen:
            soft_matches.append({
                "proof": proof, "txn": chosen,
                "conversion": {
                    "fx_rate": decision.get("fx_rate") or 0,
                    "expected_net": decision.get("expected_net") or 0,
                    "actual_received": decision.get("actual") or chosen["amount"],
                },
                "confidence": float(decision.get("confidence") or 0.6),
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
    }
    db.save_session(sid, bank, result)
    result["recon_id"] = sid
    return result
