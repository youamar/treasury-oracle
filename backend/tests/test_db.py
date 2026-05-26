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


def test_safe_dumps_decimal_round_trips_as_float():
    """R1: Decimal must come back as a number, not '4.72'."""
    import json
    from decimal import Decimal
    from app import db

    payload = {"rate": Decimal("4.72"), "qty": Decimal("100.0")}
    blob = db.safe_dumps(payload)
    restored = json.loads(blob)
    assert restored["rate"] == 4.72
    assert isinstance(restored["rate"], float)
    assert isinstance(restored["qty"], float)


def test_safe_dumps_datetime_and_uuid():
    import json
    from datetime import datetime, timezone
    from uuid import uuid4
    from app import db

    ts = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)
    uid = uuid4()
    blob = db.safe_dumps({"ts": ts, "id": uid, "tags": {"a", "b"}})
    restored = json.loads(blob)
    assert restored["ts"].startswith("2026-05-26T12:00:00")
    assert restored["id"] == str(uid)
    assert sorted(restored["tags"]) == ["a", "b"]


def test_safe_dumps_rejects_raw_bytes():
    """Persisting raw bytes via JSON loses information silently — the
    encoder must raise so the caller routes through raw_uploads instead."""
    import pytest
    from app import db

    with pytest.raises(TypeError, match="bytes"):
        db.safe_dumps({"blob": b"\x89PNG\r\n"})


def test_eval_run_persists_numeric_decimals_intact():
    """End-to-end: a Decimal that shows up in eval cases survives round-trip
    through save_eval_run / get_eval_run as a number."""
    from decimal import Decimal
    from app import db

    cases = [{"id": "c1", "expected_decision": "strict",
              "predicted_decision": "strict",
              "correct": True, "decision_correct": True,
              "expected_txn_id": "x", "predicted_txn_id": "x",
              "confidence": Decimal("0.93"),  # would have become "0.93" string before
              "tool_call_count": 2,
              "tokens_in": 100, "tokens_out": 40, "latency_ms": Decimal("123.4"),
              "notes": ""}]
    run_id = db.save_eval_run(
        label="r1-test", config_snapshot={"x": Decimal("1.5")},
        prompt_versions={}, metrics={"brier_score": Decimal("0.1")},
        cases=cases, duration_ms=1.0,
    )
    loaded = db.get_eval_run(run_id)
    assert loaded["cases"][0]["confidence"] == 0.93
    assert isinstance(loaded["cases"][0]["confidence"], float)
    assert loaded["metrics"]["brier_score"] == 0.1
    assert loaded["config_snapshot"]["x"] == 1.5


def test_pool_reuses_connection_within_thread():
    """R2: conn() must return the same underlying connection on consecutive
    calls from the same thread, so we don't pay the open cost per query."""
    from app import db
    with db.conn() as c1:
        id1 = id(c1)
    with db.conn() as c2:
        id2 = id(c2)
    assert id1 == id2


def test_concurrent_writes_survive_under_pool():
    """R2: previously this would intermittently raise 'database is locked'
    once we crossed ~20 concurrent writers because each opened its own
    fresh connection. With pooling + WAL + busy_timeout, all writers complete."""
    import concurrent.futures
    from app import db

    N = 64

    def writer(i: int) -> int:
        return db.record_error(
            source="r2-test", kind="x", message=f"m{i}",
            context={"i": i}, traceback_text=None,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        rowids = list(ex.map(writer, range(N)))

    assert len(rowids) == N
    assert len(set(rowids)) == N  # every insert got its own rowid
    errs = db.list_errors(source="r2-test", limit=N + 5)
    assert len(errs) == N


def test_pool_invalidates_when_db_path_changes(tmp_path, monkeypatch):
    """When tests monkeypatch DB_PATH, the pooled connection must drop —
    otherwise reads/writes leak into the previous test's DB file."""
    from app import db
    # Prime the pool against the test fixture's DB.
    with db.conn() as c:
        first_path = c.execute("PRAGMA database_list").fetchone()[2]

    # Swap DB_PATH to a new file and verify the next conn() opens it.
    new_db = tmp_path / "another.db"
    monkeypatch.setattr(db, "DB_PATH", new_db)
    monkeypatch.setattr(db, "_initialized", False)
    db.init_db(new_db)
    with db.conn() as c:
        second_path = c.execute("PRAGMA database_list").fetchone()[2]

    assert first_path != second_path
    assert str(new_db) in second_path.replace("\\", "/") or second_path.endswith("another.db")
