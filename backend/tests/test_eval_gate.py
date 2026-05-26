"""Continuous regression gate over the eval harness."""
import json

from app import db, eval_gate


def _seed_run(label: str, accuracy: float, cases: list[dict] | None = None) -> int:
    """Persist a synthetic eval run for the current tenant. Used to set up
    baselines without actually invoking the agent."""
    metrics = {
        "n_cases": len(cases or [1]),
        "overall_accuracy": accuracy,
        "decision_accuracy": accuracy,
        "per_class": {},
        "by_difficulty": {"hard": {"n": 1, "accuracy": accuracy}},
        "brier_score": None,
        "confidence_buckets": [],
        "mean_tool_calls": 1,
        "total_tokens_in": 0,
        "total_tokens_out": 0,
        "mean_latency_ms": 0,
    }
    return db.save_eval_run(
        label=label, config_snapshot={}, prompt_versions={},
        metrics=metrics, cases=cases or [], duration_ms=0,
    )


def _hard_case(case_id: str, correct: bool) -> dict:
    return {
        "id": case_id, "difficulty": "hard",
        "expected_decision": "strict", "predicted_decision": "strict" if correct else "no_match",
        "expected_txn_id": "x", "predicted_txn_id": "x" if correct else None,
        "decision_correct": correct, "txn_correct": correct, "correct": correct,
        "confidence": 0.9, "tool_call_count": 1,
        "tokens_in": 0, "tokens_out": 0, "latency_ms": 0, "notes": "",
    }


def test_gate_first_ever_run_passes(monkeypatch):
    """With no baseline, any first run is treated as the new baseline and passes."""
    from app import eval as _eval_mod
    monkeypatch.setattr(_eval_mod, "run_eval",
        lambda **kw: {
            "run_id": _seed_run("first", 0.85, [_hard_case("h1", True)]),
            "metrics": {
                "overall_accuracy": 0.85, "n_cases": 1,
                "by_difficulty": {"hard": {"n": 1, "accuracy": 0.85}},
            },
            "cases": [_hard_case("h1", True)],
        })

    v = eval_gate.run_gate(label="first", include_live=False)
    assert v["passes"] is True
    assert v["baseline_run_id"] is None  # no prior run
    assert v["accuracy_drop"] is None


def test_gate_passes_when_within_drop_tolerance(monkeypatch):
    """Drop of 1pp (under default 2pp tolerance) should pass."""
    from app import eval as _eval_mod
    _seed_run("baseline", 0.90, [_hard_case("h1", True)])
    monkeypatch.setattr(_eval_mod, "run_eval",
        lambda **kw: {
            "run_id": _seed_run("current", 0.89, [_hard_case("h1", True)]),
            "metrics": {
                "overall_accuracy": 0.89, "n_cases": 1,
                "by_difficulty": {"hard": {"n": 1, "accuracy": 0.89}},
            },
            "cases": [_hard_case("h1", True)],
        })

    v = eval_gate.run_gate(include_live=False)
    assert v["passes"] is True
    assert v["accuracy_drop"] is not None
    assert v["regressions"] == []


def test_gate_fails_when_accuracy_drops_past_threshold(monkeypatch):
    """5pp drop vs baseline must fail the default 2pp threshold."""
    from app import eval as _eval_mod
    _seed_run("baseline", 0.90, [_hard_case("h1", True)])
    monkeypatch.setattr(_eval_mod, "run_eval",
        lambda **kw: {
            "run_id": _seed_run("current", 0.85, [_hard_case("h1", True)]),
            "metrics": {
                "overall_accuracy": 0.85, "n_cases": 1,
                "by_difficulty": {"hard": {"n": 1, "accuracy": 0.85}},
            },
            "cases": [_hard_case("h1", True)],
        })

    v = eval_gate.run_gate(include_live=False)
    assert v["passes"] is False
    assert any("accuracy dropped" in r for r in v["regressions"])


def test_gate_fails_when_hard_fixture_regresses(monkeypatch):
    """Same overall accuracy, but a hard case flipped from ✓ to ✗ → must fail."""
    from app import eval as _eval_mod
    _seed_run("baseline", 0.85,
              [_hard_case("h1", True), _hard_case("h2", True)])
    monkeypatch.setattr(_eval_mod, "run_eval",
        lambda **kw: {
            "run_id": _seed_run("current", 0.85,
                                [_hard_case("h1", False), _hard_case("h2", True)]),
            "metrics": {
                "overall_accuracy": 0.85, "n_cases": 2,
                "by_difficulty": {"hard": {"n": 2, "accuracy": 0.50}},
            },
            "cases": [_hard_case("h1", False), _hard_case("h2", True)],
        })

    v = eval_gate.run_gate(include_live=False)
    assert v["passes"] is False
    assert any("hard-fixture" in r for r in v["regressions"])
    assert len(v["hard_case_regressions"]) == 1
    assert v["hard_case_regressions"][0]["id"] == "h1"


def test_gate_picks_best_baseline_not_most_recent(monkeypatch):
    """Baseline should be the BEST run, not the most recent. A sneaky recent
    regression shouldn't drag the baseline down."""
    from app import eval as _eval_mod
    _seed_run("good", 0.95, [_hard_case("h1", True)])     # the real baseline
    _seed_run("dip",  0.80, [_hard_case("h1", True)])     # most recent — bad
    monkeypatch.setattr(_eval_mod, "run_eval",
        lambda **kw: {
            "run_id": _seed_run("current", 0.85, [_hard_case("h1", True)]),
            "metrics": {
                "overall_accuracy": 0.85, "n_cases": 1,
                "by_difficulty": {"hard": {"n": 1, "accuracy": 0.85}},
            },
            "cases": [_hard_case("h1", True)],
        })

    v = eval_gate.run_gate(include_live=False)
    # Baseline should be the 95% run, not the 80% recent one.
    assert v["baseline_accuracy"] == 0.95
    # 95 → 85 is a 10pp drop, must fail.
    assert v["passes"] is False
    assert v["accuracy_drop"] >= 0.09


def test_gate_endpoint_returns_verdict_dict():
    """POST /api/eval/gate returns the verdict JSON."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app import eval as _eval_mod
    import pytest

    client = TestClient(app)
    # Stub eval.run_eval at the module so the gate sees a deterministic run.
    original = _eval_mod.run_eval
    try:
        _eval_mod.run_eval = lambda **kw: {
            "run_id": _seed_run("api-test", 0.95, [_hard_case("h1", True)]),
            "metrics": {
                "overall_accuracy": 0.95, "n_cases": 1,
                "by_difficulty": {"hard": {"n": 1, "accuracy": 0.95}},
            },
            "cases": [_hard_case("h1", True)],
        }
        r = client.post("/api/eval/gate", json={"include_live": False})
        assert r.status_code == 200
        j = r.json()
        assert "passes" in j
        assert "current_accuracy" in j
        assert "regressions" in j
    finally:
        _eval_mod.run_eval = original
