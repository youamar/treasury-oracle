"""Agent loop tests — mocks the LLM to drive deterministic tool-call sequences."""
import json
from types import SimpleNamespace
import pytest

from app import agent as agent_mod
from app import db


def _make_client(script):
    """
    Build a stub OpenAI-compatible client that returns successive responses
    from `script`. Each entry is either:
      - {"tool_calls": [{"name": "...", "arguments": {...}}]}
      - {"content": "final json string"}
    """
    state = {"i": 0}

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    s = script[state["i"]]
                    state["i"] += 1
                    if "tool_calls" in s:
                        tcs = [SimpleNamespace(
                            id=f"call_{i}", type="function",
                            function=SimpleNamespace(
                                name=tc["name"], arguments=json.dumps(tc["arguments"])
                            )) for i, tc in enumerate(s["tool_calls"])]
                        msg = SimpleNamespace(content="", tool_calls=tcs)
                    else:
                        msg = SimpleNamespace(content=s["content"], tool_calls=None)
                    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])
    return _Client()


def test_agent_strict_match_via_tools(sample_proof, sample_txn, monkeypatch):
    expected_net = round(1000 * 4.72 * 0.995, 2)
    script = [
        {"tool_calls": [{"name": "get_fx_rate",
                         "arguments": {"from_ccy": "USD", "to_ccy": "MYR",
                                       "date": "2026-05-20"}}]},
        {"tool_calls": [{"name": "apply_bank_fee",
                         "arguments": {"amount": 4720.0, "bank_name": "Maybank"}}]},
        {"content": json.dumps({
            "decision": "strict", "txn_index": 0,
            "fx_rate": 4.72, "fee_amount": 23.6,
            "expected_net": expected_net, "actual": sample_txn["amount"],
            "confidence": 0.99, "fuzzy_signals": [], "swift_route": None,
            "reasoning": "Within tolerance after fees.",
        })},
    ]
    monkeypatch.setattr(agent_mod, "get_client",
                        lambda use_fallback=False: _make_client(script))

    out = agent_mod.reconcile_agent([sample_proof], [sample_txn], "Maybank")
    assert out["summary"]["matched"] == 1
    assert out["matches"][0]["txn"]["id"] == sample_txn["id"]
    assert out["matches"][0]["agent_tool_calls"][0]["name"] == "get_fx_rate"
    assert out["mode"] == "agent"

    # Trace was persisted to DB
    trace = db.get_trace(out["recon_id"])
    types = [t["type"] for t in trace]
    assert "user_prompt" in types and "tool_call" in types and "decision" in types


def test_agent_handles_no_candidates(sample_proof, monkeypatch):
    monkeypatch.setattr(agent_mod, "get_client",
                        lambda use_fallback=False: _make_client([]))
    out = agent_mod.reconcile_agent([sample_proof], [], "Maybank")
    assert out["summary"]["matched"] == 0
    assert out["summary"]["unmatched_proofs"] == 1


def test_agent_skips_unreadable_proof():
    out = agent_mod.reconcile_agent(
        [{"error": "ocr failed", "source_file": "x.png"}], [], "Maybank")
    assert out["summary"]["unmatched_proofs"] == 1


def test_agent_exhausts_step_budget_then_fallback(sample_proof, sample_txn, monkeypatch):
    # Always returns garbled non-JSON content — agent should run out of steps
    script = [{"content": "I don't know"}] * (agent_mod.MAX_STEPS + 2)
    monkeypatch.setattr(agent_mod, "get_client",
                        lambda use_fallback=False: _make_client(script))
    out = agent_mod.reconcile_agent([sample_proof], [sample_txn], "Maybank")
    assert out["summary"]["unmatched_proofs"] == 1


def test_agent_discrepancy_with_swift_trace(monkeypatch):
    proof = {"amount": 500, "currency": "USD", "date": "2026-05-24",
             "payer": "X", "reference": "Y", "source_file": "p.png"}
    expected = 500 * 4.72 * 0.995
    txn = {"id": "txn_0", "date": "2026-05-24",
           "amount": round(expected * 0.7, 2), "currency": "MYR",
           "description": "MYSTERY", "reference": "?"}
    script = [
        {"tool_calls": [{"name": "trace_swift_route", "arguments": {
            "source_currency": "USD", "sent_amount": 500,
            "expected_net_local": round(expected, 2),
            "actual_net_local": round(expected * 0.7, 2),
            "fx_rate": 4.72, "local_currency": "MYR",
        }}]},
        {"content": json.dumps({
            "decision": "discrepancy", "txn_index": 0,
            "fx_rate": 4.72, "fee_amount": 23.6,
            "expected_net": round(expected, 2),
            "actual": round(expected * 0.7, 2),
            "confidence": 0.4, "fuzzy_signals": [],
            "swift_route": {"nodes": [{"name": "x"}]},
            "reasoning": "Large gap, attributed to correspondent fees.",
        })},
    ]
    monkeypatch.setattr(agent_mod, "get_client",
                        lambda use_fallback=False: _make_client(script))
    out = agent_mod.reconcile_agent([proof], [txn], "Maybank")
    assert out["summary"]["matched"] == 0
    assert out["unmatched_proofs"][0]["swift_route"] is not None
