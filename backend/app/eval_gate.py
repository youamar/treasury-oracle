"""Continuous regression gate over the eval harness.

Run after any prompt edit, model swap, skill toggle, or config change. The
gate runs eval and compares against the **best** historical run for this
tenant — not just the previous one — so that a slow drift doesn't sneak
past a per-run diff.

Two failure modes the gate catches:

  1. Aggregate regression — overall accuracy dropped by more than
     `max_accuracy_drop` (default 2 percentage points) vs the best run.
  2. Hard-case regression — any adversarial fixture that previously passed
     now fails. Hard cases are the canary; losing one matters even if
     overall accuracy is unchanged.

Usage (CLI):
    python -m app.eval_gate            # exits 1 on regression
    python -m app.eval_gate --label x  # tag the run

Usage (HTTP):
    POST /api/eval/gate                # returns {passes, regressions, ...}
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

from . import db, eval as _eval


DEFAULT_MAX_ACCURACY_DROP = 0.02       # 2 percentage points
DEFAULT_MIN_ABSOLUTE_ACCURACY = 0.0    # set >0 to enforce a hard floor


def _best_baseline(runs: list[dict]) -> dict | None:
    """Pick the run with the highest overall_accuracy as the baseline. Ties
    broken by most recent."""
    if not runs:
        return None
    return max(runs, key=lambda r: (
        r["metrics"].get("overall_accuracy", 0),
        r.get("created_at", ""),
    ))


def _hard_case_regressions(current_cases: list[dict],
                           baseline_cases: list[dict]) -> list[dict]:
    """Cases that were ✓ in baseline but ✗ now, scoped to hard difficulty."""
    by_id_baseline = {c["id"]: c for c in baseline_cases or []}
    out = []
    for c in current_cases or []:
        if c.get("difficulty") != "hard":
            continue
        prev = by_id_baseline.get(c["id"])
        if prev is None:
            continue
        if prev.get("correct") and not c.get("correct"):
            out.append({
                "id": c["id"],
                "expected": c.get("expected_decision"),
                "predicted_now": c.get("predicted_decision"),
                "predicted_baseline": prev.get("predicted_decision"),
            })
    return out


def run_gate(label: str = "",
             max_accuracy_drop: float = DEFAULT_MAX_ACCURACY_DROP,
             min_absolute_accuracy: float = DEFAULT_MIN_ABSOLUTE_ACCURACY,
             include_live: bool = True) -> dict:
    """Execute eval, evaluate against baseline, return the gate verdict.

    The verdict is JSON-safe (already plain dicts / floats / strings) so it
    can be marshalled by the API endpoint or pretty-printed by the CLI."""
    history = db.list_eval_runs(limit=50)
    baseline = _best_baseline(history)

    current = _eval.run_eval(
        label=label or "regression-gate", include_live=include_live,
    )
    cur_acc = current["metrics"].get("overall_accuracy", 0.0)
    cur_hard = (current["metrics"].get("by_difficulty") or {}).get("hard", {})

    regressions: list[str] = []
    hard_regressions: list[dict] = []
    drop = None

    if baseline:
        # Fetch the baseline's per-case verdicts (list_eval_runs returns
        # metrics + meta, not cases — pull the full row).
        full_baseline = db.get_eval_run(baseline["id"]) or {}
        baseline_cases = full_baseline.get("cases") or []
        hard_regressions = _hard_case_regressions(
            current.get("cases") or [], baseline_cases
        )
        base_acc = baseline["metrics"].get("overall_accuracy", 0.0)
        drop = round(base_acc - cur_acc, 4)
        if drop > max_accuracy_drop:
            regressions.append(
                f"overall accuracy dropped {drop*100:.2f}pp vs run #{baseline['id']} "
                f"({base_acc*100:.2f}% → {cur_acc*100:.2f}%)"
            )
        if hard_regressions:
            regressions.append(
                f"{len(hard_regressions)} hard-fixture case{'s' if len(hard_regressions)!=1 else ''} "
                f"regressed: {', '.join(r['id'] for r in hard_regressions[:3])}"
                + (f" (+ {len(hard_regressions)-3} more)" if len(hard_regressions) > 3 else "")
            )

    if cur_acc < min_absolute_accuracy:
        regressions.append(
            f"absolute accuracy {cur_acc*100:.2f}% below floor {min_absolute_accuracy*100:.2f}%"
        )

    return {
        "passes": not regressions,
        "current_run_id": current.get("run_id"),
        "current_accuracy": cur_acc,
        "current_hard_accuracy": cur_hard.get("accuracy"),
        "baseline_run_id": baseline["id"] if baseline else None,
        "baseline_accuracy": baseline["metrics"].get("overall_accuracy") if baseline else None,
        "accuracy_drop": drop,
        "max_accuracy_drop": max_accuracy_drop,
        "min_absolute_accuracy": min_absolute_accuracy,
        "regressions": regressions,
        "hard_case_regressions": hard_regressions,
        "n_cases": current["metrics"].get("n_cases", 0),
    }


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="", help="label for this run")
    parser.add_argument("--max-drop", type=float, default=DEFAULT_MAX_ACCURACY_DROP,
                        help="max allowed accuracy drop vs baseline (default 0.02)")
    parser.add_argument("--min-acc", type=float, default=DEFAULT_MIN_ABSOLUTE_ACCURACY,
                        help="hard floor for absolute accuracy (default 0)")
    parser.add_argument("--no-live", action="store_true",
                        help="exclude live operator-confirmed fixtures")
    args = parser.parse_args()

    verdict = run_gate(
        label=args.label,
        max_accuracy_drop=args.max_drop,
        min_absolute_accuracy=args.min_acc,
        include_live=not args.no_live,
    )

    print(f"\n{'='*60}")
    print(f"REGRESSION GATE — {'PASS ✓' if verdict['passes'] else 'FAIL ✗'}")
    print(f"{'='*60}")
    print(f"  Current run:       #{verdict['current_run_id']}  "
          f"acc {verdict['current_accuracy']*100:.2f}%  "
          f"({verdict['n_cases']} cases)")
    if verdict["baseline_run_id"]:
        print(f"  Baseline run:      #{verdict['baseline_run_id']}  "
              f"acc {verdict['baseline_accuracy']*100:.2f}%")
        print(f"  Accuracy drop:     {verdict['accuracy_drop']*100:+.2f}pp "
              f"(max allowed {verdict['max_accuracy_drop']*100:.2f}pp)")
    else:
        print(f"  Baseline run:      (none — this is the first run)")
    if verdict["regressions"]:
        print(f"\n  Regressions:")
        for r in verdict["regressions"]:
            print(f"    • {r}")
    if verdict["hard_case_regressions"]:
        print(f"\n  Hard-case details:")
        for r in verdict["hard_case_regressions"]:
            print(f"    • {r['id']}: was {r['predicted_baseline']!r}, now {r['predicted_now']!r}")
    print()
    return 0 if verdict["passes"] else 1


if __name__ == "__main__":
    sys.exit(_cli())
