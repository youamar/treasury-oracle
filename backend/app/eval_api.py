"""Eval API — run the harness, list runs, diff vs previous."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import db, eval as _eval, calibration


router = APIRouter(prefix="/api/eval", tags=["eval"])


class EvalRunBody(BaseModel):
    label: str = ""
    config_override: dict | None = None
    temperature: float | None = None   # default 0 for reproducibility


@router.post("/run")
def run_eval(body: EvalRunBody):
    return _eval.run_eval(
        label=body.label,
        config_override=body.config_override,
        temperature=body.temperature,
    )


@router.get("/runs")
def list_runs(limit: int = 20):
    return {"runs": db.list_eval_runs(limit=limit)}


@router.get("/runs/{run_id}")
def get_run(run_id: int):
    r = db.get_eval_run(run_id)
    if r is None:
        raise HTTPException(404, "run not found")
    return r


@router.get("/diff/{run_id}")
def diff_run(run_id: int):
    """Compare run_id to the previous run."""
    cur = db.get_eval_run(run_id)
    if cur is None:
        raise HTTPException(404, "run not found")
    runs = db.list_eval_runs(limit=50)
    prev = next((r for r in runs if r["id"] < cur["id"]), None)
    if prev is None:
        return {"current": cur, "previous": None, "deltas": None}
    cm = cur["metrics"]
    pm = prev["metrics"]
    deltas = {
        "overall_accuracy": round(cm["overall_accuracy"] - pm["overall_accuracy"], 4),
        "decision_accuracy": round(cm["decision_accuracy"] - pm["decision_accuracy"], 4),
        "mean_tool_calls": round(cm["mean_tool_calls"] - pm["mean_tool_calls"], 3),
        "mean_latency_ms": round(cm["mean_latency_ms"] - pm["mean_latency_ms"], 1),
        "total_tokens_in": cm["total_tokens_in"] - pm["total_tokens_in"],
        "total_tokens_out": cm["total_tokens_out"] - pm["total_tokens_out"],
        "brier_score": (round(cm["brier_score"] - pm["brier_score"], 4)
                        if cm.get("brier_score") is not None
                        and pm.get("brier_score") is not None else None),
    }
    return {"current": cur, "previous": prev, "deltas": deltas}


# ---------- calibration ----------

class CalibrateBody(BaseModel):
    run_id: int


@router.post("/calibrate")
def calibrate(body: CalibrateBody):
    try:
        return calibration.fit_from_eval_run(body.run_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/calibrator")
def calibrator():
    return calibration.calibrator_status()


@router.delete("/calibrator")
def calibrator_reset():
    calibration.reset()
    return {"ok": True}
