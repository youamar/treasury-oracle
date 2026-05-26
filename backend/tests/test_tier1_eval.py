"""Tier-1 DS infrastructure: prompt versioning, telemetry, eval harness."""
import json
from fastapi.testclient import TestClient

from app.main import app
from app import db, platform_config, eval as _eval


client = TestClient(app)


# ---------- prompt versioning ----------

def test_save_config_persists_prompt_version():
    cfg = platform_config.load_config()
    cfg["skill_overrides"] = {"dunning_email": {"system_prompt": "custom tone v1"}}
    platform_config.save_config(cfg, edited_by="user")

    rows = db.list_prompt_versions(skill_id="dunning_email")
    assert any(r["prompt_text"] == "custom tone v1" for r in rows)


def test_prompt_version_hash_stable():
    h1 = platform_config._hash_prompt("hello world")
    h2 = platform_config._hash_prompt("hello world")
    assert h1 == h2 and len(h1) == 16


def test_active_prompt_versions_covers_all_skills():
    versions = platform_config.active_prompt_versions()
    from app.skills import all_skills
    assert set(versions.keys()) == {s.id for s in all_skills()}


def test_overriding_prompt_changes_version_hash():
    cfg = platform_config.load_config()
    base_versions = platform_config.active_prompt_versions(cfg)
    cfg["skill_overrides"] = {"dunning_email": {"system_prompt": "VERY DIFFERENT"}}
    platform_config.save_config(cfg)
    new_versions = platform_config.active_prompt_versions()
    assert new_versions["dunning_email"] != base_versions["dunning_email"]


# ---------- telemetry ----------

def test_record_metric_round_trip():
    db.record_metric("test-sess", proof_index=0, step=1, skill_id="get_fx_rate",
                     tokens_in=100, tokens_out=20, latency_ms=12.5, status="ok")
    m = db.session_metrics("test-sess")
    assert m["total_tokens_in"] == 100
    assert m["per_skill"]["get_fx_rate"]["calls"] == 1


def test_agent_emits_metrics(sample_proof, sample_txn):
    from app.agent import reconcile_agent
    result = reconcile_agent([sample_proof], [sample_txn], "default")
    assert "metrics" in result
    # stub LLM returns immediately so at least 1 LLM step should be recorded
    assert result["metrics"]["n_steps"] >= 1


def test_agent_result_includes_prompt_versions(sample_proof, sample_txn):
    from app.agent import reconcile_agent
    r = reconcile_agent([sample_proof], [sample_txn], "default")
    pv = r.get("prompt_versions") or {}
    assert "get_fx_rate" in pv and len(pv["get_fx_rate"]) == 16


# ---------- eval harness ----------

def test_load_fixtures_finds_packs():
    fx = _eval.load_fixtures()
    ids = {c["id"] for c in fx if not c.get("load_error")}
    assert {"01_strict_usd_myr", "05_no_match_no_candidates"} <= ids


def test_aggregate_handles_empty():
    out = _eval._aggregate([])
    assert out["n_cases"] == 0
    assert out["overall_accuracy"] == 0


def test_aggregate_per_class_metrics():
    verdicts = [
        {"id": "a", "expected_decision": "strict", "predicted_decision": "strict",
         "correct": True, "decision_correct": True, "confidence": 0.9,
         "tool_call_count": 2, "tokens_in": 100, "tokens_out": 20, "latency_ms": 50,
         "expected_txn_id": "x", "predicted_txn_id": "x"},
        {"id": "b", "expected_decision": "strict", "predicted_decision": "no_match",
         "correct": False, "decision_correct": False, "confidence": 0.0,
         "tool_call_count": 1, "tokens_in": 80, "tokens_out": 10, "latency_ms": 30,
         "expected_txn_id": "y", "predicted_txn_id": None},
    ]
    agg = _eval._aggregate(verdicts)
    pc = agg["per_class"]
    assert pc["strict"]["recall"] == 0.5
    assert pc["strict"]["precision"] == 1.0
    assert pc["no_match"]["precision"] == 0.0


def test_run_eval_persists_and_returns_metrics():
    out = _eval.run_eval(label="smoke")
    assert out["run_id"] > 0
    assert out["metrics"]["n_cases"] == len(_eval.load_fixtures())
    runs = db.list_eval_runs(limit=5)
    assert any(r["id"] == out["run_id"] for r in runs)


def test_eval_run_endpoint_and_diff():
    a = client.post("/api/eval/run", json={"label": "a"}).json()
    b = client.post("/api/eval/run", json={"label": "b"}).json()
    r = client.get(f"/api/eval/diff/{b['run_id']}").json()
    assert r["current"]["id"] == b["run_id"]
    assert r["previous"]["id"] == a["run_id"]
    assert r["deltas"] is not None


def test_eval_run_endpoint_returns_404_for_unknown():
    r = client.get("/api/eval/runs/999999")
    assert r.status_code == 404
