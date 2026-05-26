"""Agent evaluation harness — runs the reconciliation agent against labeled
fixtures and reports per-class precision/recall, confidence calibration, mean
tool-call counts, latency, and token usage.

Why this exists:
  Every prompt edit, model swap, or skill toggle changes agent behavior. Without
  an evaluation harness, every change is a vibe. The harness gives Treasury
  Oracle a measurable feedback loop: run before, change, run after, diff.

Fixture format (tests/fixtures/agent_eval/*.json):
  {
    "id": "slug",
    "notes": "...",
    "bank": "Maybank",
    "proof": {... reconciliation_agent proof shape ...},
    "txn_candidates": [{... txn shape, must include 'id' ...}],
    "expected_decision": "strict" | "soft" | "discrepancy" | "no_match",
    "expected_txn_id": "txn_xxx" | null
  }
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .agent import reconcile_agent, EVAL_AGENT_TEMPERATURE
from . import db, platform_config


FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "agent_eval"

_CLASSES = ("strict", "soft", "discrepancy", "no_match")


def load_fixtures(directory: Path | None = None,
                  include_live: bool = True) -> list[dict]:
    d = directory or FIXTURE_DIR
    out = []
    if d.exists():
        for p in sorted(d.glob("*.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception as e:
                out.append({"id": p.stem, "load_error": str(e)})
    if include_live:
        for f in db.list_live_fixtures(limit=500):
            out.append({
                "id": f["fixture_id"],
                "difficulty": f.get("difficulty", "live"),
                "notes": (f.get("notes") or "") + " [live]",
                "bank": f.get("bank") or "default",
                "proof": f["proof"],
                "txn_candidates": f["txn_candidates"],
                "expected_decision": f["expected_decision"],
                "expected_txn_id": f.get("expected_txn_id"),
            })
    return out


def _case_verdict(case: dict, result: dict) -> dict:
    """Decide what the agent produced for this case, and whether it matches truth."""
    expected = case["expected_decision"]
    expected_txn = case.get("expected_txn_id")
    difficulty = case.get("difficulty", "easy")

    matches = result.get("matches") or []
    softs = result.get("soft_matches") or []
    unmatched_proofs = result.get("unmatched_proofs") or []
    metrics = result.get("metrics") or {}

    if matches:
        m = matches[0]
        predicted = "strict"
        predicted_txn = (m.get("txn") or {}).get("id")
        confidence = float(m.get("confidence") or 0)
        tool_calls = m.get("agent_tool_calls") or []
    elif softs:
        s = softs[0]
        predicted = "soft"
        predicted_txn = (s.get("txn") or {}).get("id")
        confidence = float(s.get("confidence") or 0)
        tool_calls = s.get("agent_tool_calls") or []
    elif unmatched_proofs:
        up = unmatched_proofs[0]
        # Distinguish discrepancy (has closest_txn / swift_route or reason hints) from no_match
        if up.get("closest_txn") or up.get("swift_route") or up.get("expected_net"):
            predicted = "discrepancy"
        else:
            predicted = "no_match"
        predicted_txn = (up.get("closest_txn") or {}).get("id")
        confidence = 0.0
        tool_calls = up.get("agent_tool_calls") or []
    else:
        predicted = "no_match"
        predicted_txn = None
        confidence = 0.0
        tool_calls = []

    txn_correct = (expected_txn is None) or (predicted_txn == expected_txn)
    decision_correct = predicted == expected
    correct = decision_correct and (decision_correct and txn_correct if expected == "strict" else True)

    return {
        "id": case["id"],
        "difficulty": difficulty,
        "expected_decision": expected,
        "predicted_decision": predicted,
        "expected_txn_id": expected_txn,
        "predicted_txn_id": predicted_txn,
        "decision_correct": decision_correct,
        "txn_correct": txn_correct,
        "correct": correct,
        "confidence": confidence,
        "tool_call_count": len(tool_calls),
        "tokens_in": metrics.get("total_tokens_in", 0),
        "tokens_out": metrics.get("total_tokens_out", 0),
        "latency_ms": metrics.get("total_latency_ms", 0.0),
        "notes": case.get("notes", ""),
    }


def _aggregate(verdicts: list[dict]) -> dict:
    """Per-class precision/recall/F1, confidence buckets, Brier, and rollups."""
    n = len(verdicts) or 1
    overall_acc = sum(1 for v in verdicts if v["correct"]) / n
    decision_acc = sum(1 for v in verdicts if v["decision_correct"]) / n

    per_class: dict[str, dict] = {c: {"tp": 0, "fp": 0, "fn": 0, "support": 0}
                                  for c in _CLASSES}
    for v in verdicts:
        exp = v["expected_decision"]
        pred = v["predicted_decision"]
        if exp in per_class:
            per_class[exp]["support"] += 1
        if pred in per_class:
            if pred == exp:
                per_class[pred]["tp"] += 1
            else:
                per_class[pred]["fp"] += 1
                if exp in per_class:
                    per_class[exp]["fn"] += 1

    def _prf(d: dict) -> dict:
        tp, fp, fn = d["tp"], d["fp"], d["fn"]
        p = tp / (tp + fp) if (tp + fp) else 0.0
        r = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        return {"precision": round(p, 4), "recall": round(r, 4),
                "f1": round(f1, 4), "support": d["support"],
                "tp": tp, "fp": fp, "fn": fn}

    per_class_metrics = {c: _prf(d) for c, d in per_class.items()}

    # Confidence calibration — Brier score over correctness as 0/1, only on
    # cases where the agent produced a confidence (strict/soft)
    confident_cases = [v for v in verdicts if v["confidence"] > 0]
    if confident_cases:
        brier = sum(
            (v["confidence"] - (1.0 if v["correct"] else 0.0)) ** 2
            for v in confident_cases
        ) / len(confident_cases)
    else:
        brier = None

    # Confidence-bucket accuracy
    buckets = [(0.0, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.01)]
    bucket_stats = []
    for lo, hi in buckets:
        in_b = [v for v in verdicts if lo <= v["confidence"] < hi]
        if not in_b:
            continue
        bucket_stats.append({
            "range": f"{lo:.1f}-{hi:.2f}",
            "n": len(in_b),
            "accuracy": round(sum(1 for v in in_b if v["correct"]) / len(in_b), 4),
            "mean_confidence": round(sum(v["confidence"] for v in in_b) / len(in_b), 4),
        })

    # Easy vs hard split — judges care more about adversarial accuracy than
    # the happy path. A model that aces only the easy set is suspect.
    easy = [v for v in verdicts if v.get("difficulty") != "hard"]
    hard = [v for v in verdicts if v.get("difficulty") == "hard"]
    by_difficulty = {}
    if easy:
        by_difficulty["easy"] = {
            "n": len(easy),
            "accuracy": round(sum(1 for v in easy if v["correct"]) / len(easy), 4),
        }
    if hard:
        by_difficulty["hard"] = {
            "n": len(hard),
            "accuracy": round(sum(1 for v in hard if v["correct"]) / len(hard), 4),
        }

    return {
        "n_cases": len(verdicts),
        "overall_accuracy": round(overall_acc, 4),
        "decision_accuracy": round(decision_acc, 4),
        "per_class": per_class_metrics,
        "by_difficulty": by_difficulty,
        "brier_score": round(brier, 4) if brier is not None else None,
        "confidence_buckets": bucket_stats,
        "mean_tool_calls": round(sum(v["tool_call_count"] for v in verdicts) / n, 3),
        "total_tokens_in": sum(v["tokens_in"] for v in verdicts),
        "total_tokens_out": sum(v["tokens_out"] for v in verdicts),
        "mean_latency_ms": round(sum(v["latency_ms"] for v in verdicts) / n, 1),
    }


def run_eval(label: str = "", config_override: dict | None = None,
             fixtures: list[dict] | None = None,
             include_live: bool = True,
             temperature: float | None = None) -> dict:
    """Run every fixture through the agent and persist an EvalReport.

    `temperature` defaults to EVAL_AGENT_TEMPERATURE (0.0) so reruns are
    reproducible and run-to-run deltas reflect prompt/config changes rather
    than sampling noise. Pass a non-zero value to measure stability.
    """
    cases = fixtures if fixtures is not None else load_fixtures(include_live=include_live)
    cfg_used = config_override or platform_config.load_config()
    eval_temp = temperature if temperature is not None else EVAL_AGENT_TEMPERATURE
    # Snapshot the temperature into the run config so future diffs can spot
    # an apples-to-oranges comparison.
    cfg_used = {**cfg_used, "agent_temperature": eval_temp}
    versions = platform_config.active_prompt_versions(cfg_used)
    t0 = time.perf_counter()
    verdicts: list[dict] = []
    for case in cases:
        if case.get("load_error"):
            verdicts.append({"id": case["id"], "load_error": case["load_error"],
                             "correct": False, "decision_correct": False,
                             "expected_decision": "?", "predicted_decision": "?",
                             "expected_txn_id": None, "predicted_txn_id": None,
                             "confidence": 0.0, "tool_call_count": 0,
                             "tokens_in": 0, "tokens_out": 0, "latency_ms": 0,
                             "notes": ""})
            continue
        sid = f"eval-{case['id']}-{uuid.uuid4().hex[:6]}"
        try:
            result = reconcile_agent(
                proofs=[case["proof"]],
                txns=case["txn_candidates"],
                bank=case.get("bank", "default"),
                session_id=sid,
                config_override=cfg_used,
                temperature=eval_temp,
            )
            verdicts.append(_case_verdict(case, result))
        except Exception as e:
            verdicts.append({
                "id": case["id"], "run_error": str(e),
                "correct": False, "decision_correct": False,
                "expected_decision": case.get("expected_decision", "?"),
                "predicted_decision": "error",
                "expected_txn_id": case.get("expected_txn_id"),
                "predicted_txn_id": None,
                "confidence": 0.0, "tool_call_count": 0,
                "tokens_in": 0, "tokens_out": 0, "latency_ms": 0,
                "notes": case.get("notes", ""),
            })

    duration_ms = (time.perf_counter() - t0) * 1000
    metrics = _aggregate(verdicts)
    run_id = db.save_eval_run(
        label=label, config_snapshot=cfg_used, prompt_versions=versions,
        metrics=metrics, cases=verdicts, duration_ms=duration_ms,
    )
    return {
        "run_id": run_id, "label": label,
        "metrics": metrics, "cases": verdicts,
        "prompt_versions": versions,
        "duration_ms": round(duration_ms, 1),
    }
