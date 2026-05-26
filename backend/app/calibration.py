"""Confidence calibration via isotonic regression.

LLMs are notoriously overconfident — when our agent says "confidence: 0.9",
it's often wrong 30%+ of the time. After an eval run we can fit an isotonic
regression `f: raw_confidence → calibrated_confidence` and post-process every
decision through it. Brier score before/after is reported.

Why isotonic instead of Platt:
  - Doesn't assume a sigmoid shape (LLM confidence distributions aren't sigmoid)
  - Monotonic — preserves rankings
  - Robust on small samples (works down to ~30 points)

Coefficients persist in the `calibrators` table per tenant per scope. `apply()`
loads them once per process and caches.
"""
from __future__ import annotations

import json
import threading
from typing import Any

from . import db


_cache_lock = threading.Lock()
_cache: dict[str, tuple[str, list[dict] | None]] = {}  # tenant -> (etag, payload)


def _isotonic_to_points(iso) -> list[dict]:
    """Serialize a fitted sklearn IsotonicRegression to a portable list of
    (x, y) breakpoints we can re-evaluate without sklearn at inference time."""
    xs = list(map(float, iso.X_thresholds_))
    ys = list(map(float, iso.y_thresholds_))
    return [{"x": x, "y": y} for x, y in zip(xs, ys)]


def _piecewise_apply(points: list[dict], x: float) -> float:
    """Linearly interpolate between breakpoints; clamp at endpoints."""
    if not points:
        return x
    if x <= points[0]["x"]:
        return points[0]["y"]
    if x >= points[-1]["x"]:
        return points[-1]["y"]
    for i in range(1, len(points)):
        a, b = points[i - 1], points[i]
        if a["x"] <= x <= b["x"]:
            if b["x"] == a["x"]:
                return a["y"]
            t = (x - a["x"]) / (b["x"] - a["x"])
            return a["y"] + t * (b["y"] - a["y"])
    return x


def _brier(pairs: list[tuple[float, int]]) -> float | None:
    if not pairs:
        return None
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def fit_from_eval_run(run_id: int) -> dict:
    """Fit a calibrator on the cases of one eval run. Stores it as the
    'global' scope calibrator for the active tenant."""
    run = db.get_eval_run(run_id)
    if run is None:
        raise ValueError(f"eval run {run_id} not found")

    cases = run["cases"]
    pairs = [
        (float(c.get("confidence") or 0.0), 1 if c.get("correct") else 0)
        for c in cases
        if c.get("confidence", 0) > 0
    ]
    if len(pairs) < 5:
        return {"ok": False, "reason": "need at least 5 confident cases",
                "n_samples": len(pairs)}

    brier_before = _brier(pairs)

    try:
        from sklearn.isotonic import IsotonicRegression
    except ImportError:
        return {"ok": False, "reason": "sklearn not installed"}

    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(xs, ys)
    points = _isotonic_to_points(iso)
    after = [(_piecewise_apply(points, x), y) for x, y in pairs]
    brier_after = _brier(after)

    db.save_calibrator(
        scope="global", method="isotonic",
        coefficients={"points": points},
        n_samples=len(pairs),
        brier_before=brier_before, brier_after=brier_after,
        source_run_id=run_id,
    )
    _invalidate_cache(db.current_tenant())
    return {
        "ok": True,
        "scope": "global",
        "method": "isotonic",
        "n_samples": len(pairs),
        "brier_before": round(brier_before, 4) if brier_before is not None else None,
        "brier_after": round(brier_after, 4) if brier_after is not None else None,
        "improvement": (round(brier_before - brier_after, 4)
                        if brier_before is not None and brier_after is not None
                        else None),
        "points": points,
    }


def _invalidate_cache(tenant: str | None = None):
    with _cache_lock:
        if tenant:
            _cache.pop(tenant, None)
        else:
            _cache.clear()


def _load_for_tenant() -> dict[str, list[dict]] | None:
    """Return {scope: points} for the current tenant, cached per-process."""
    tenant = db.current_tenant()
    with _cache_lock:
        cached = _cache.get(tenant)
    cals = db.load_calibrators()
    if not cals:
        with _cache_lock:
            _cache[tenant] = ("empty", None)
        return None
    out = {scope: c["coefficients"].get("points", []) for scope, c in cals.items()}
    with _cache_lock:
        _cache[tenant] = ("loaded", out)
    return out


def apply(raw_confidence: float, scope: str = "global") -> float:
    """Apply the active calibrator. If none exists, return raw."""
    if raw_confidence is None:
        return raw_confidence
    cals = _load_for_tenant()
    if not cals:
        return float(raw_confidence)
    points = cals.get(scope) or cals.get("global") or []
    if not points:
        return float(raw_confidence)
    return float(_piecewise_apply(points, float(raw_confidence)))


def calibrator_status() -> dict:
    """For UI: which calibrator is active, with last-known Brier."""
    cals = db.load_calibrators()
    out = {}
    for scope, c in cals.items():
        out[scope] = {
            "method": c["method"],
            "n_samples": c["n_samples"],
            "brier_before": c["brier_before"],
            "brier_after": c["brier_after"],
            "source_run_id": c["source_run_id"],
            "created_at": c["created_at"],
            "points": c["coefficients"].get("points", []),
        }
    return out


def reset():
    db.delete_calibrators()
    _invalidate_cache()
