"""SQLite layer CRUD + persistence tests."""
import json
from app import db


def _result_skel(**overrides):
    base = {
        "matches": [], "soft_matches": [], "unmatched_proofs": [],
        "unmatched_txns": [], "trace": [],
        "summary": {"total_proofs": 0, "total_txns": 0, "matched": 0,
                    "soft_matches": 0, "unmatched_proofs": 0, "unmatched_txns": 0},
    }
    base.update(overrides)
    return base


def test_init_idempotent():
    db.init_db()
    db.init_db()  # no error


def test_session_roundtrip(sample_proof, sample_txn):
    result = _result_skel(
        matches=[{
            "proof": sample_proof, "txn": sample_txn,
            "conversion": {"fx_rate": 4.72, "expected_net": 4696.4,
                           "expected_gross": 4720.0, "actual_received": 4696.4,
                           "fee_pct": 0.005, "fee_amount": 23.6},
            "confidence": 0.99, "reasoning": "test", "status": "matched",
        }],
        unmatched_txns=[{"id": "txn_orphan", "amount": 50, "currency": "MYR",
                         "date": "2026-05-20", "description": "o", "reference": ""}],
        summary={"total_proofs": 1, "total_txns": 2, "matched": 1, "soft_matches": 0,
                 "unmatched_proofs": 0, "unmatched_txns": 1},
        trace=["line1"],
    )
    db.save_session("sid1", "Maybank", result)

    s = db.load_session("sid1")
    assert s["bank"] == "Maybank"
    assert s["summary"]["matched"] == 1
    assert len(s["matches"]) == 1
    assert s["matches"][0]["proof"]["reference"] == "INV-2026-001"
    assert len(s["unmatched_txns"]) == 1


def test_load_missing_session():
    assert db.load_session("nope") is None


def test_promote_soft_match(sample_proof, sample_txn):
    result = _result_skel(
        soft_matches=[{
            "proof": sample_proof, "txn": sample_txn,
            "conversion": {"fx_rate": 4.72, "expected_net": 4500.0,
                           "actual_received": 4200.0},
            "confidence": 0.85, "signals": ["ref match"], "reasoning": "soft",
        }],
        summary={"total_proofs": 1, "total_txns": 1, "matched": 0, "soft_matches": 1,
                 "unmatched_proofs": 0, "unmatched_txns": 0},
    )
    db.save_session("sid2", "Maybank", result)
    s = db.load_session("sid2")
    sid = s["soft_matches"][0]["id"]

    promoted = db.promote_soft_match(sid)
    assert promoted is not None
    assert len(promoted["matches"]) == 1
    assert promoted["matches"][0]["status"] == "matched_via_soft"
    assert promoted["soft_matches"][0]["confirmed"] is True


def test_alias_persistence():
    db.remember_alias("acme", "acme global")
    assert db.lookup_alias("acme") == "acme global"
    assert "acme" in db.all_aliases()


def test_alias_upsert():
    db.remember_alias("x", "a")
    db.remember_alias("x", "b")
    assert db.lookup_alias("x") == "b"


def test_campaign_crud():
    db.upsert_campaign({
        "id": "c1", "client_name": "Acme", "invoice_ref": "INV-1",
        "invoice_amount": 1000, "invoice_ccy": "USD", "outstanding": 100,
        "due_date": "2026-05-01", "current_stage": 0, "status": "active",
        "history": [{"subject": "s", "body": "b"}], "created_at": "2026-05-01",
    })
    c = db.get_campaign("c1")
    assert c["client_name"] == "Acme"
    assert c["history"][0]["subject"] == "s"
    assert db.list_campaigns_db()[0]["id"] == "c1"


def test_watcher_crud():
    db.upsert_watcher({"id": "w1", "from_ccy": "USD", "to_ccy": "MYR",
                       "target_rate": 4.8, "note": "test"})
    ws = db.list_watchers()
    assert len(ws) == 1 and ws[0]["target_rate"] == 4.8
    db.delete_watcher("w1")
    assert db.list_watchers() == []


def test_agent_trace():
    db.append_trace("sidx", "p1.png", 0, "user_prompt", {"foo": 1})
    db.append_trace("sidx", "p1.png", 1, "tool_call",
                    {"name": "get_fx_rate", "arguments": {"from_ccy": "USD"}})
    db.append_trace("sidx", "p1.png", 1, "tool_result",
                    {"name": "get_fx_rate", "result": {"rate": 4.72}})
    t = db.get_trace("sidx")
    assert len(t) == 3
    assert t[1]["payload"]["name"] == "get_fx_rate"
