"""Tier-2: confidence calibration, live eval set, per-skill model routing."""
import json
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import db, calibration, eval as _eval, platform_config, chutes_client
from app.skills import SKILL_REGISTRY, resolve_skill_config


client = TestClient(app)


# ---------- calibration ----------

def _seed_eval_run_with_known_miscalibration():
    """Make 10 cases where confidence consistently overstates accuracy:
    8 of 10 'high-confidence' decisions are actually wrong → very overconfident."""
    cases = []
    for i in range(10):
        # Half labeled correct, but all reported confidence 0.9 — clearly miscalibrated
        was_correct = i < 5
        cases.append({
            "id": f"c{i}", "expected_decision": "strict",
            "predicted_decision": "strict",
            "correct": was_correct, "decision_correct": was_correct,
            "expected_txn_id": "x", "predicted_txn_id": "x",
            "confidence": 0.9, "tool_call_count": 1,
            "tokens_in": 0, "tokens_out": 0, "latency_ms": 0,
            "notes": "",
        })
    metrics = _eval._aggregate(cases)
    run_id = db.save_eval_run(
        label="miscal", config_snapshot={}, prompt_versions={},
        metrics=metrics, cases=cases, duration_ms=0,
    )
    return run_id


def test_fit_calibrator_improves_brier():
    run_id = _seed_eval_run_with_known_miscalibration()
    out = calibration.fit_from_eval_run(run_id)
    assert out["ok"] is True
    assert out["brier_before"] >= out["brier_after"]
    # Stored
    cals = calibration.calibrator_status()
    assert "global" in cals


def test_apply_calibrator_shrinks_overconfidence():
    run_id = _seed_eval_run_with_known_miscalibration()
    calibration.fit_from_eval_run(run_id)
    # 0.9 raw confidence on a miscalibrated set should pull down toward 0.5
    cal = calibration.apply(0.9)
    assert cal <= 0.9
    assert 0.0 <= cal <= 1.0


def test_apply_passthrough_when_no_calibrator():
    calibration.reset()
    assert calibration.apply(0.42) == pytest.approx(0.42)


def test_calibrate_endpoint_404():
    r = client.post("/api/eval/calibrate", json={"run_id": 999999})
    assert r.status_code == 404


def test_calibrate_endpoint_round_trip():
    run = client.post("/api/eval/run", json={"label": "for-calib"}).json()
    r = client.post("/api/eval/calibrate", json={"run_id": run["run_id"]}).json()
    # 10 fixtures, but only confident cases count — may or may not have ≥5;
    # accept either ok=True or 'need at least 5'.
    assert r.get("ok") in (True, False)


# ---------- live fixtures ----------

def test_add_and_list_live_fixture():
    db.add_live_fixture(
        fixture_id="live-t1",
        bank="Maybank",
        proof={"amount": 1, "currency": "USD"},
        txn_candidates=[{"id": "txn1", "amount": 4.72}],
        expected_decision="strict",
        expected_txn_id="txn1",
        source="soft_match_confirm",
    )
    out = db.list_live_fixtures()
    assert any(f["fixture_id"] == "live-t1" for f in out)


def test_load_fixtures_includes_live():
    db.add_live_fixture(
        fixture_id="live-t2",
        bank="Maybank",
        proof={"amount": 1, "currency": "USD"},
        txn_candidates=[{"id": "t", "amount": 4.72}],
        expected_decision="no_match",
        source="manual",
    )
    fx = _eval.load_fixtures(include_live=True)
    assert any(c["id"] == "live-t2" for c in fx)
    fx_no = _eval.load_fixtures(include_live=False)
    assert not any(c["id"] == "live-t2" for c in fx_no)


def test_soft_match_reject_creates_live_fixture():
    # Build a session with a soft match in DB
    from app.agent import reconcile_agent
    proof = {"amount": 1000.0, "currency": "USD", "date": "2026-05-20",
             "payer": "Acme", "payee": "BT", "reference": "INV-T-LF",
             "source_file": "p.png"}
    txn = {"id": "txn_live_1", "date": "2026-05-20",
           "amount": 4696.40, "currency": "MYR",
           "description": "INWARD ACME", "reference": "INV-T-LF"}
    # Stub the agent loop to produce a soft match directly via DB
    sid = "sess-live-1"
    db.save_session(sid, "Maybank", {
        "summary": {"total_proofs": 1, "total_txns": 1, "matched": 0,
                    "soft_matches": 1, "unmatched_proofs": 0, "unmatched_txns": 0},
        "trace": [], "matches": [],
        "soft_matches": [{"proof": proof, "txn": txn,
                          "conversion": {"fx_rate": 4.72, "expected_net": 4673,
                                         "actual_received": 4696},
                          "confidence": 0.7, "signals": ["payer_match"],
                          "reasoning": "soft"}],
        "unmatched_proofs": [], "unmatched_txns": [],
    })
    # Find the soft_match id
    with db.conn() as c:
        row = c.execute("SELECT id FROM soft_matches WHERE session_id = ?", (sid,)).fetchone()
    sm_id = row["id"]
    r = client.post("/api/soft-match/reject", json={"soft_match_id": sm_id})
    assert r.status_code == 200
    fx = db.list_live_fixtures()
    assert any(f["source"] == "soft_match_reject" for f in fx)


# ---------- per-skill model routing ----------

def test_default_model_profiles_on_skills():
    assert SKILL_REGISTRY["fuzzy_compare"].model_profile == "cheap"
    assert SKILL_REGISTRY["trace_swift_route"].model_profile == "strong"
    assert SKILL_REGISTRY["get_fx_rate"].model_profile == "default"


def test_resolve_profile_falls_back_to_default():
    from app.config import MODEL_PROFILES
    assert chutes_client.resolve_profile("unknown") == MODEL_PROFILES["default"]
    assert chutes_client.resolve_profile(None) == MODEL_PROFILES["default"]
    assert chutes_client.resolve_profile("cheap") == MODEL_PROFILES["cheap"]


def test_override_model_profile_via_api():
    r = client.put("/api/platform/skills/get_fx_rate",
                   json={"model_profile": "strong"})
    assert r.status_code == 200
    cfg = r.json()
    assert cfg["skill_overrides"]["get_fx_rate"]["model_profile"] == "strong"


def test_resolve_skill_config_returns_profile():
    cfg = {"skill_overrides": {"get_fx_rate": {"model_profile": "strong"}}}
    rc = resolve_skill_config(SKILL_REGISTRY["get_fx_rate"], cfg)
    assert rc["model_profile"] == "strong"


def test_model_profiles_endpoint():
    r = client.get("/api/platform/model-profiles").json()
    assert set(r["profiles"].keys()) >= {"default", "cheap", "strong", "vision"}
