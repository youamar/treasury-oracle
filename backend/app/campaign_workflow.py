"""Autonomous dunning campaign workflow — LangGraph over our skill registry.

Why LangGraph here (and only here):
  Campaigns need to run across days, pause between stages, resume after a
  process restart, and survive an entire weekend without losing state. That
  pattern — durable, resumable, multi-step workflow — is what LangGraph's
  StateGraph + checkpointer is genuinely good at. Our hand-rolled reconciliation
  loop stays untouched; LangGraph orchestrates a workflow on top of skills.

Flow:
    START → load → decide → draft_and_send → wait_for_response → decide → ...
                       │                                              │
                       └── (status=paid / stages exhausted) ── finalize → END

Each `tick` invocation runs one cycle (decide → draft_and_send → wait) and
interrupts BEFORE wait_for_response. Calling `tick` again resumes from there,
runs `wait_for_response` (currently a no-op so demo advances instantly), and
loops back to `decide`. In production `wait_for_response` would interrupt for
N real days or be triggered by inbound payment events.
"""
from __future__ import annotations

import threading
from typing import TypedDict, Annotated

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver

from . import db, reliability
from .campaign import _draft_stage, STAGES


def _safe_node(name):
    """Decorator: any exception in a node becomes a clean error-state update."""
    def deco(fn):
        def wrapped(state: dict) -> dict:
            try:
                return fn(state)
            except BaseException as e:
                reliability.record_error(
                    f"campaign_workflow.{name}", e,
                    context={"campaign_id": state.get("campaign_id"),
                             "stage": state.get("current_stage")},
                )
                return {
                    "status": "error",
                    "last_action": f"error:{name}",
                    "error_message": f"{type(e).__name__}: {e}"[:300],
                    "log": [f"node {name} failed: {type(e).__name__}: {e}"],
                }
        wrapped.__name__ = fn.__name__
        return wrapped
    return deco


# ---------- state ----------

def _append(left: list, right: list) -> list:
    return (left or []) + (right or [])


class CampaignState(TypedDict, total=False):
    campaign_id: str
    tenant_id: str
    status: str                # 'active' | 'paid' | 'exhausted' | 'error' | 'missing'
    current_stage: int         # next stage index to send
    iterations: int            # how many cycles the workflow has run
    last_action: str
    error_message: str         # populated when a node fails
    log: Annotated[list[str], _append]


# ---------- nodes ----------

@_safe_node("load")
def _load(state: CampaignState) -> dict:
    # If the workflow already errored, keep the error sticky — don't let a
    # fresh DB read reset us back to 'active'.
    if state.get("status") == "error":
        return {"log": ["load skipped — workflow in error state"]}
    with db.tenant_scope(state.get("tenant_id") or "default"):
        c = db.get_campaign(state["campaign_id"])
    if not c:
        return {"status": "missing", "log": [f"campaign {state['campaign_id']} not found"]}
    return {
        "status": c["status"],
        "current_stage": c["current_stage"],
        "log": [f"loaded campaign — stage {c['current_stage']}, status {c['status']}"],
    }


@_safe_node("decide")
def _decide(state: CampaignState) -> dict:
    iters = state.get("iterations", 0) + 1
    return {"iterations": iters, "last_action": "decide"}


def _route(state: CampaignState) -> str:
    if state.get("status") in ("paid", "missing", "exhausted", "error"):
        return "finalize"
    if state.get("current_stage", 0) >= len(STAGES):
        return "finalize"
    return "draft_and_send"


@_safe_node("draft_and_send")
def _draft_and_send(state: CampaignState) -> dict:
    with db.tenant_scope(state.get("tenant_id") or "default"):
        c = db.get_campaign(state["campaign_id"])
        if not c:
            return {"status": "missing", "log": ["campaign disappeared mid-flight"]}
        idx = c["current_stage"]
        # If the latest history entry for this stage is unsent, mark it sent;
        # otherwise draft a new one for this stage.
        if c["history"] and not c["history"][-1].get("sent") \
                and c["history"][-1].get("stage_index") == idx:
            c["history"][-1]["sent"] = True
            stage_done = idx
        else:
            drafted = _draft_stage(idx, c)
            c["history"].append({**drafted, "sent": True})
            stage_done = idx
        c["current_stage"] = idx + 1
        if c["current_stage"] >= len(STAGES):
            c["status"] = "exhausted"
        db.upsert_campaign(c)
    return {
        "current_stage": c["current_stage"],
        "status": c["status"],
        "last_action": f"sent_stage_{stage_done}",
        "log": [f"sent stage {stage_done + 1}/{len(STAGES)}"],
    }


@_safe_node("wait_for_response")
def _wait_for_response(state: CampaignState) -> dict:
    """Where a real campaign would pause for N days. NoOp in the demo —
    the workflow interrupts BEFORE this node, so the actual wait happens
    between ticks."""
    return {"last_action": "wait_done", "log": ["wait window elapsed"]}


@_safe_node("finalize")
def _finalize(state: CampaignState) -> dict:
    with db.tenant_scope(state.get("tenant_id") or "default"):
        c = db.get_campaign(state["campaign_id"])
        if c and c["status"] not in ("paid", "exhausted"):
            c["status"] = "exhausted"
            db.upsert_campaign(c)
    return {"last_action": "finalized", "log": ["workflow complete"]}


# ---------- graph + checkpointer ----------

_graph_lock = threading.Lock()
_built_graph = None


def _build_graph():
    g = StateGraph(CampaignState)
    g.add_node("load", _load)
    g.add_node("decide", _decide)
    g.add_node("draft_and_send", _draft_and_send)
    g.add_node("wait_for_response", _wait_for_response)
    g.add_node("finalize", _finalize)

    g.add_edge(START, "load")
    g.add_edge("load", "decide")
    g.add_conditional_edges("decide", _route,
                            {"draft_and_send": "draft_and_send",
                             "finalize": "finalize"})
    g.add_edge("draft_and_send", "wait_for_response")
    # After the wait, reload from DB before deciding — picks up any out-of-band
    # status changes (payment received, manually marked paid, etc.).
    g.add_edge("wait_for_response", "load")
    g.add_edge("finalize", END)
    return g


def _checkpointer():
    """SqliteSaver bound to our main DB file. Auto-creates its own tables."""
    return SqliteSaver.from_conn_string(str(db.DB_PATH))


def _compiled():
    global _built_graph
    with _graph_lock:
        if _built_graph is None:
            _built_graph = _build_graph()
    return _built_graph


def _thread_config(campaign_id: str, tenant_id: str) -> dict:
    return {"configurable": {"thread_id": f"{tenant_id}:{campaign_id}"}}


# ---------- public API ----------

def start(campaign_id: str) -> dict:
    """Begin the workflow. Runs the first cycle, interrupts before the wait."""
    tenant = db.current_tenant()
    initial: CampaignState = {
        "campaign_id": campaign_id,
        "tenant_id": tenant,
        "iterations": 0,
        "log": ["workflow started"],
    }
    graph = _compiled()
    with _checkpointer() as saver:
        app = graph.compile(
            checkpointer=saver,
            interrupt_before=["wait_for_response"],
        )
        cfg = _thread_config(campaign_id, tenant)
        state = app.invoke(initial, cfg)
        snap = app.get_state(cfg)
    return _snapshot(state, snap)


def tick(campaign_id: str) -> dict:
    """Resume the workflow from its last checkpoint and run one more cycle."""
    tenant = db.current_tenant()
    graph = _compiled()
    with _checkpointer() as saver:
        app = graph.compile(
            checkpointer=saver,
            interrupt_before=["wait_for_response"],
        )
        cfg = _thread_config(campaign_id, tenant)
        existing = app.get_state(cfg)
        if existing is None or not existing.values:
            return start(campaign_id)
        state = app.invoke(None, cfg)
        snap = app.get_state(cfg)
    return _snapshot(state, snap)


def get_state(campaign_id: str) -> dict | None:
    tenant = db.current_tenant()
    graph = _compiled()
    with _checkpointer() as saver:
        app = graph.compile(checkpointer=saver,
                            interrupt_before=["wait_for_response"])
        snap = app.get_state(_thread_config(campaign_id, tenant))
    if snap is None or not snap.values:
        return None
    return _snapshot(snap.values, snap)


def recover(campaign_id: str) -> dict:
    """Clear an error state so the workflow can resume.

    Wipes `status`/`error_message`, then ticks once to re-load from DB. If the
    workflow wasn't in error, this is a no-op tick.
    """
    tenant = db.current_tenant()
    graph = _compiled()
    with _checkpointer() as saver:
        app = graph.compile(checkpointer=saver,
                            interrupt_before=["wait_for_response"])
        cfg = _thread_config(campaign_id, tenant)
        snap = app.get_state(cfg)
        if snap is None or not snap.values:
            return start(campaign_id)
        # Overwrite error markers in the checkpoint. as_node="load" makes the
        # next step come out of load → decide cleanly.
        app.update_state(cfg, {
            "status": None,
            "error_message": None,
            "last_action": "recovered",
            "log": ["operator triggered recovery"],
        }, as_node="load")
        state = app.invoke(None, cfg)
        snap = app.get_state(cfg)
    return _snapshot(state, snap)


def stop(campaign_id: str) -> dict:
    """Force the workflow to its terminal state. Marks campaign exhausted if
    it was still active. Idempotent."""
    tenant = db.current_tenant()
    with db.tenant_scope(tenant):
        c = db.get_campaign(campaign_id)
        if c and c["status"] == "active":
            c["status"] = "exhausted"
            db.upsert_campaign(c)
    return {"campaign_id": campaign_id, "status": "stopped"}


def _snapshot(state_values, snap) -> dict:
    next_nodes = list(snap.next) if snap is not None and snap.next else []
    return {
        "campaign_id": state_values.get("campaign_id"),
        "tenant_id": state_values.get("tenant_id"),
        "status": state_values.get("status"),
        "current_stage": state_values.get("current_stage"),
        "iterations": state_values.get("iterations"),
        "last_action": state_values.get("last_action"),
        "log": state_values.get("log", []),
        "interrupted_before": next_nodes,
        "done": not next_nodes,
        "error_message": state_values.get("error_message"),
    }
