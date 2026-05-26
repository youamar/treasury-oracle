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


def test_agent_attaches_provenance(sample_proof, sample_txn, monkeypatch):
    """F1: every match carries a provenance block tracing each numeric to
    its source (ECB, bank statement, config, or 'agent_unverified')."""
    import json
    from app import agent as agent_mod

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
    prov = out["matches"][0]["conversion"]["provenance"]
    assert prov["fx_rate"]["source"] == "ecb_live"  # via fixture stub
    assert prov["fx_rate"]["trusted"] is True
    assert prov["fee"]["source"].startswith("config:BANK_FEES.")
    assert prov["actual_received"]["source"].startswith("bank_statement:")
    assert prov["proof_amount"]["source"].startswith("ocr:")
    assert prov["all_inputs_trusted"] is True


def test_agent_marks_unverified_when_llm_skips_fx_tool(sample_proof, sample_txn, monkeypatch):
    """If the LLM reports an fx_rate without calling get_fx_rate, provenance
    must flag it as agent_unverified — that's our defense against hallucinated rates."""
    import json
    from app import agent as agent_mod

    # No tool calls — LLM jumps straight to a decision with a made-up rate.
    script = [
        {"content": json.dumps({
            "decision": "strict", "txn_index": 0,
            "fx_rate": 4.72, "fee_amount": 23.6,
            "expected_net": round(1000 * 4.72 * 0.995, 2),
            "actual": sample_txn["amount"],
            "confidence": 0.99, "fuzzy_signals": [], "swift_route": None,
            "reasoning": "Vibes.",
        })},
    ]
    monkeypatch.setattr(agent_mod, "get_client",
                        lambda use_fallback=False: _make_client(script))

    out = agent_mod.reconcile_agent([sample_proof], [sample_txn], "Maybank")
    # Strict allowed here (the guardrail only fires on explicit untrusted FX tool
    # results, not absence-of-tool-call). But provenance must say so.
    m = out["matches"] + out["soft_matches"]
    assert m, "expected some match/soft entry"
    prov = m[0]["conversion"]["provenance"]
    assert prov["fx_rate"]["source"] == "agent_unverified"
    assert prov["fx_rate"]["trusted"] is False
    assert prov["all_inputs_trusted"] is False


def test_agent_uses_configured_temperature(sample_proof, sample_txn, monkeypatch):
    """R3: temperature can be overridden per-call (eval uses 0)."""
    import json
    from app import agent as agent_mod

    captured: dict = {}

    class _Captured:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    captured["temperature"] = kw.get("temperature")
                    class _M:
                        content = json.dumps({"decision": "no_match", "confidence": 0,
                                              "reasoning": "stub"})
                        tool_calls = None
                    class _R: choices = [type("C", (), {"message": _M})]
                    return _R()

    monkeypatch.setattr(agent_mod, "get_client", lambda use_fallback=False: _Captured())

    agent_mod.reconcile_agent([sample_proof], [sample_txn], "Maybank",
                              temperature=0.0)
    assert captured["temperature"] == 0.0

    captured.clear()
    agent_mod.reconcile_agent([sample_proof], [sample_txn], "Maybank",
                              temperature=0.7)
    assert captured["temperature"] == 0.7


def test_eval_runs_at_temperature_zero_by_default(monkeypatch, tmp_path):
    """R3: eval.run_eval defaults to deterministic temperature."""
    import json
    from app import agent as agent_mod, eval as eval_mod

    captured: list = []

    class _Captured:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    captured.append(kw.get("temperature"))
                    class _M:
                        content = json.dumps({"decision": "no_match", "confidence": 0,
                                              "reasoning": "stub"})
                        tool_calls = None
                    class _R: choices = [type("C", (), {"message": _M})]
                    return _R()

    monkeypatch.setattr(agent_mod, "get_client", lambda use_fallback=False: _Captured())

    fx = [{
        "id": "f1", "bank": "Maybank",
        "proof": {"amount": 100.0, "currency": "USD", "date": "2026-05-20",
                  "payer": "x", "payee": "y", "reference": "r",
                  "source_file": "p.png"},
        "txn_candidates": [{"id": "txn_0", "date": "2026-05-20", "amount": 472,
                            "currency": "MYR", "description": "x", "reference": "r"}],
        "expected_decision": "no_match",
    }]
    eval_mod.run_eval(label="r3-test", fixtures=fx, include_live=False)
    assert captured, "agent was never called"
    assert all(t == 0.0 for t in captured), f"non-zero temperature in eval: {captured}"


def test_low_quality_ocr_skips_agent(sample_proof, sample_txn, monkeypatch):
    """F9: a proof with ocr_quality.gate='low_quality' must NOT reach the LLM."""
    import json
    from app import agent as agent_mod

    llm_called = []
    class _Spy:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    llm_called.append(True)
                    class _M:
                        content = json.dumps({"decision": "strict", "txn_index": 0,
                                              "confidence": 0.99, "reasoning": "x"})
                        tool_calls = None
                    class _R: choices = [type("C", (), {"message": _M})]
                    return _R()
    monkeypatch.setattr(agent_mod, "get_client", lambda use_fallback=False: _Spy())

    proof = {**sample_proof, "ocr_quality": {
        "completeness": 0.3, "missing_fields": ["payer", "date"], "gate": "low_quality",
    }}
    out = agent_mod.reconcile_agent([proof], [sample_txn], "Maybank")
    assert llm_called == [], "agent must not invoke the LLM for low-quality OCR"
    assert out["summary"]["matched"] == 0
    assert out["summary"]["unmatched_proofs"] == 1
    assert out["unmatched_proofs"][0].get("needs_review") is True
    assert "human review" in out["unmatched_proofs"][0]["reason"].lower()


def test_high_quality_ocr_proceeds(sample_proof, sample_txn, monkeypatch):
    """Inverse: a complete proof should reach the LLM as normal."""
    import json
    from app import agent as agent_mod

    llm_called = []
    class _Stub:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    llm_called.append(True)
                    class _M:
                        content = json.dumps({"decision": "no_match", "confidence": 0,
                                              "reasoning": "x"})
                        tool_calls = None
                    class _R: choices = [type("C", (), {"message": _M})]
                    return _R()
    monkeypatch.setattr(agent_mod, "get_client", lambda use_fallback=False: _Stub())

    proof = {**sample_proof, "ocr_quality": {
        "completeness": 0.95, "missing_fields": [], "gate": "ok",
    }}
    agent_mod.reconcile_agent([proof], [sample_txn], "Maybank")
    assert llm_called, "agent should call the LLM when OCR quality passes"


def test_verifier_downgrades_when_no_tool_calls(sample_proof, sample_txn, monkeypatch):
    """F2: LLM jumps straight to strict without calling get_fx_rate / fuzzy →
    verifier flags 'answered from priors only' and downgrades to soft."""
    import json
    from app import agent as agent_mod
    script = [
        {"content": json.dumps({
            "decision": "strict", "txn_index": 0,
            "fx_rate": 4.72, "fee_amount": 23.6,
            "expected_net": round(1000 * 4.72 * 0.995, 2),
            "actual": sample_txn["amount"],
            "confidence": 0.99, "fuzzy_signals": [], "swift_route": None,
            "reasoning": "Just looks right.",
        })},
    ]
    monkeypatch.setattr(agent_mod, "get_client",
                        lambda use_fallback=False: _make_client(script))
    out = agent_mod.reconcile_agent([sample_proof], [sample_txn], "Maybank")
    # Strict claim downgraded to soft
    assert out["summary"]["matched"] == 0
    assert out["summary"]["soft_matches"] == 1
    v = out["soft_matches"][0]["conversion"]["provenance"]["verifier"]
    assert v["verdict"] == "downgrade"
    assert any("tool call" in c for c in v["concerns"])


def test_verifier_downgrades_when_diff_above_strict_ceiling(sample_proof, sample_txn,
                                                            monkeypatch):
    """diff% above 0.5% should not be 'strict' — verifier downgrades."""
    import json
    from app import agent as agent_mod
    script = [
        {"tool_calls": [{"name": "get_fx_rate",
                         "arguments": {"from_ccy": "USD", "to_ccy": "MYR",
                                       "date": "2026-05-20"}}]},
        {"content": json.dumps({
            "decision": "strict", "txn_index": 0,
            "fx_rate": 4.72, "fee_amount": 23.6,
            "expected_net": 4000.0,    # actual is 4696.40 → ~17% off
            "actual": sample_txn["amount"],
            "confidence": 0.95, "fuzzy_signals": [], "swift_route": None,
            "reasoning": "x",
        })},
    ]
    monkeypatch.setattr(agent_mod, "get_client",
                        lambda use_fallback=False: _make_client(script))
    out = agent_mod.reconcile_agent([sample_proof], [sample_txn], "Maybank")
    assert out["summary"]["soft_matches"] == 1
    concerns = out["soft_matches"][0]["conversion"]["provenance"]["verifier"]["concerns"]
    assert any("diff" in c for c in concerns)


def test_verifier_confirms_clean_strict_match(sample_proof, sample_txn, monkeypatch):
    """Tight diff, same day, real tool calls, ref overlap → verifier confirms."""
    import json
    from app import agent as agent_mod
    # Make sure the txn description includes the proof reference + payer.
    txn = {**sample_txn, "description": "INWARD TT ACME CORP INV-2026-001",
           "reference": "INV-2026-001"}
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
            "expected_net": expected_net, "actual": txn["amount"],
            "confidence": 0.99, "fuzzy_signals": [], "swift_route": None,
            "reasoning": "Within tolerance after fees.",
        })},
    ]
    monkeypatch.setattr(agent_mod, "get_client",
                        lambda use_fallback=False: _make_client(script))
    out = agent_mod.reconcile_agent([sample_proof], [txn], "Maybank")
    assert out["summary"]["matched"] == 1
    v = out["matches"][0]["conversion"]["provenance"]["verifier"]
    assert v["verdict"] == "confirm"
    assert v["concerns"] == []


def test_verifier_skips_non_strict_decisions(sample_proof, sample_txn, monkeypatch):
    """Verifier doesn't second-guess no_match / soft / discrepancy."""
    import json
    from app import agent as agent_mod
    script = [
        {"content": json.dumps({
            "decision": "no_match", "txn_index": None,
            "confidence": 0.2, "reasoning": "doesn't match",
        })},
    ]
    monkeypatch.setattr(agent_mod, "get_client",
                        lambda use_fallback=False: _make_client(script))
    out = agent_mod.reconcile_agent([sample_proof], [sample_txn], "Maybank")
    assert out["summary"]["unmatched_proofs"] == 1
