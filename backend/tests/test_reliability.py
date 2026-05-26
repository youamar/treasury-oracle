"""Reliability primitives: retry, classification, error logging, upload guardrails,
workflow error states."""
import io
from fastapi.testclient import TestClient

from app.main import app
from app import db, reliability, uploads as _uploads
from app import campaign, campaign_workflow as cwf


client = TestClient(app)


# ---------- error classification ----------

class _StatusErr(Exception):
    def __init__(self, sc): super().__init__("boom"); self.status_code = sc


def test_is_retryable_status_codes():
    assert reliability.is_retryable(_StatusErr(429))
    assert reliability.is_retryable(_StatusErr(503))
    assert reliability.is_retryable(_StatusErr(500))
    assert not reliability.is_retryable(_StatusErr(400))
    assert not reliability.is_retryable(_StatusErr(404))


def test_is_auth_error():
    assert reliability.is_auth_error(_StatusErr(401))
    assert reliability.is_auth_error(_StatusErr(403))
    assert not reliability.is_auth_error(_StatusErr(429))


def test_is_retryable_by_name():
    class ConnectionError(Exception): pass
    class RateLimitError(Exception): pass
    assert reliability.is_retryable(ConnectionError())
    assert reliability.is_retryable(RateLimitError())


# ---------- retry executor ----------

def test_with_retry_succeeds_on_second_attempt(monkeypatch):
    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        if calls["n"] < 2:
            raise _StatusErr(503)
        return "ok"
    policy = reliability.RetryPolicy(max_attempts=3, base_delay=0, max_delay=0, jitter=0)
    out = reliability.with_retry(fn, policy=policy)
    assert out == "ok"
    assert calls["n"] == 2


def test_with_retry_gives_up_after_max(monkeypatch):
    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        raise _StatusErr(503)
    policy = reliability.RetryPolicy(max_attempts=3, base_delay=0, max_delay=0, jitter=0)
    try:
        reliability.with_retry(fn, policy=policy)
    except _StatusErr:
        pass
    assert calls["n"] == 3


def test_with_retry_does_not_retry_non_retryable():
    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        raise _StatusErr(400)
    policy = reliability.RetryPolicy(max_attempts=5, base_delay=0, max_delay=0, jitter=0)
    try:
        reliability.with_retry(fn, policy=policy)
    except _StatusErr:
        pass
    assert calls["n"] == 1


# ---------- error logging ----------

def test_record_error_persists():
    eid = db.record_error("test.source", "TestKind", "something failed",
                          context={"k": "v"}, traceback_text="trace")
    rows = db.list_errors(limit=10)
    assert any(r["id"] == eid for r in rows)
    row = [r for r in rows if r["id"] == eid][0]
    assert row["context"] == {"k": "v"}
    assert row["source"] == "test.source"


def test_list_errors_tenant_scoped():
    with db.tenant_scope("alpha"):
        db.record_error("s", "K", "alpha-err")
    with db.tenant_scope("beta"):
        assert not any(r["message"] == "alpha-err" for r in db.list_errors())


def test_clear_errors_tenant_scoped():
    with db.tenant_scope("c1"):
        db.record_error("s", "K", "e1")
        n = db.clear_errors()
        assert n >= 1
        assert db.list_errors() == []


def test_errors_endpoints():
    client.delete("/api/memory/errors")
    db.record_error("api.test", "K", "endpoint test")
    r = client.get("/api/memory/errors").json()
    assert any(e["message"] == "endpoint test" for e in r["errors"])
    r = client.delete("/api/memory/errors").json()
    assert r["cleared"] >= 1


# ---------- upload guardrails ----------

def test_upload_size_rejected():
    big = b"x" * (_uploads.MAX_FILE_SIZE_BYTES + 1)
    try:
        _uploads.validate_file("big.png", len(big), _uploads.ALLOWED_PROOF_EXTS)
    except _uploads.UploadRejected as e:
        assert e.status_code == 413
        return
    raise AssertionError("should have raised")


def test_upload_mime_rejected():
    try:
        _uploads.validate_file("hack.exe", 100, _uploads.ALLOWED_PROOF_EXTS)
    except _uploads.UploadRejected as e:
        assert e.status_code == 415
        return
    raise AssertionError("should have raised")


def test_extract_proofs_rejects_oversize_via_api():
    big = b"x" * (_uploads.MAX_FILE_SIZE_BYTES + 1)
    files = {"files": ("big.png", io.BytesIO(big), "image/png")}
    r = client.post("/api/extract-proofs", files=files)
    assert r.status_code == 413


def test_extract_proofs_rejects_bad_extension_via_api():
    files = {"files": ("hack.exe", io.BytesIO(b"abc"), "application/octet-stream")}
    r = client.post("/api/extract-proofs", files=files)
    assert r.status_code == 415


# ---------- workflow error handling ----------

def test_workflow_load_failure_sets_error_state(monkeypatch):
    c = campaign.create_campaign(
        client_name="X", invoice_ref="INV-ERR-1",
        invoice_amount=100, invoice_ccy="USD", outstanding=10,
        due_date="2026-05-01",
    )
    # Force the draft node to blow up
    import app.campaign_workflow as cwf_mod

    def boom(*a, **k): raise RuntimeError("synthetic draft failure")
    monkeypatch.setattr(cwf_mod, "_draft_stage", boom)

    # start sends the pre-drafted stage 0; ticking forces stage-1 drafting,
    # which is where our boom takes effect.
    cwf_mod.start(c["id"])
    snap = cwf_mod.tick(c["id"])
    assert snap["status"] == "error", snap
    assert snap["error_message"] and "synthetic draft failure" in snap["error_message"]
    # next tick: should route to finalize because status=='error'
    snap2 = cwf_mod.tick(c["id"])
    assert snap2["done"] is True


# ---------- chutes client narrow fallback ----------

def test_chutes_chat_does_not_swap_on_non_auth(monkeypatch):
    from app import chutes_client as cc
    calls = {"primary": 0, "fallback": 0}

    def primary(messages, model, **kw):
        calls["primary"] += 1
        raise _StatusErr(400)
    def fallback(messages, model, **kw):
        calls["fallback"] += 1
        return "should not happen"

    monkeypatch.setattr(cc, "_call_primary", primary)
    monkeypatch.setattr(cc, "_call_fallback", fallback)
    monkeypatch.setattr(cc, "chat", cc._real_chat)  # restore real impl over conftest mock
    try:
        cc.chat([{"role": "user", "content": "hi"}], model="m")
    except _StatusErr:
        pass
    assert calls["primary"] == 1
    assert calls["fallback"] == 0


def test_chutes_chat_swaps_on_auth_error(monkeypatch):
    from app import chutes_client as cc
    calls = {"primary": 0, "fallback": 0}

    def primary(messages, model, **kw):
        calls["primary"] += 1
        raise _StatusErr(401)
    def fallback(messages, model, **kw):
        calls["fallback"] += 1
        return "ok"

    monkeypatch.setattr(cc, "_call_primary", primary)
    monkeypatch.setattr(cc, "_call_fallback", fallback)
    monkeypatch.setattr(cc, "CHUTES_API_KEY_FALLBACK", "present")
    monkeypatch.setattr(cc, "chat", cc._real_chat)  # restore real impl over conftest mock
    out = cc.chat([{"role": "user", "content": "hi"}], model="m")
    assert out == "ok"
    assert calls["fallback"] == 1


def test_breaker_state_persists_in_db():
    """R-3: breaker state is written to SQLite, not a module dict."""
    from app import reliability as rel, db
    # Reset any prior state for this source so the test is hermetic.
    db.delete_breaker_state("test.db.breaker")
    rel.configure_breaker("test.db.breaker", rel.BreakerConfig(
        failure_threshold=2, cooldown_seconds=999))

    # Two retryable failures should open the breaker.
    err = _StatusErr(503)
    rel._record_outcome("test.db.breaker", ok=False, err=err)
    rel._record_outcome("test.db.breaker", ok=False, err=err)

    # State is in the DB, not just the process.
    row = db.get_breaker_state("test.db.breaker")
    assert row["state"] == "open"
    assert row["failures"] == 2

    # And the snapshot endpoint sees it.
    snap = rel.breaker_snapshot()
    src_row = next((s for s in snap if s["source"] == "test.db.breaker"), None)
    assert src_row is not None
    assert src_row["state"] == "open"


def test_breaker_admit_rejects_when_open():
    """Once open, _admit raises BreakerOpen until cooldown elapses."""
    from app import reliability as rel, db
    db.delete_breaker_state("test.admit.open")
    rel.configure_breaker("test.admit.open", rel.BreakerConfig(
        failure_threshold=1, cooldown_seconds=999))
    # Push to open
    rel._record_outcome("test.admit.open", ok=False, err=_StatusErr(503))
    import pytest
    with pytest.raises(rel.BreakerOpen):
        rel._admit("test.admit.open")


def test_breaker_success_closes_after_recovery():
    """A successful call clears failures and returns the breaker to closed."""
    from app import reliability as rel, db
    db.delete_breaker_state("test.recover")
    rel.configure_breaker("test.recover", rel.BreakerConfig(
        failure_threshold=2, cooldown_seconds=1))
    rel._record_outcome("test.recover", ok=False, err=_StatusErr(503))
    rel._record_outcome("test.recover", ok=True)
    assert db.get_breaker_state("test.recover")["state"] == "closed"
    assert db.get_breaker_state("test.recover")["failures"] == 0


def test_upload_gc_drops_old_bytes(tmp_path):
    """S-3: gc_old_uploads removes both DB rows and storage bytes when an
    upload is older than the cutoff AND not referenced by a match."""
    from datetime import datetime, timezone, timedelta
    import hashlib
    from app import db, uploads
    from pathlib import Path

    payload = b"GC-TEST-bytes" + b"a" * 100
    sha = hashlib.sha256(payload).hexdigest()
    rec = uploads.store_bytes("gc.png", payload, purpose="proof")
    # Hand-edit the row to look old.
    old_iso = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(timespec="seconds")
    with db.conn() as c:
        c.execute("UPDATE raw_uploads SET uploaded_at = ? WHERE id = ?",
                  (old_iso, rec["id"]))

    assert Path(rec["storage_path"]).exists()

    out = uploads.gc_old_uploads(older_than_days=30, keep_sessions=False)
    assert out["rows_deleted"] >= 1
    assert out["bytes_deleted"] >= len(payload)
    assert not Path(rec["storage_path"]).exists()
    assert db.find_upload_by_sha(sha) is None


def test_upload_gc_keeps_referenced_uploads():
    """When the SHA is still cited by a persisted match, GC must skip it."""
    from datetime import datetime, timezone, timedelta
    import hashlib, json
    from app import db, uploads
    from pathlib import Path

    payload = b"GC-KEEP-bytes" + b"b" * 100
    sha = hashlib.sha256(payload).hexdigest()
    rec = uploads.store_bytes("keep.png", payload, purpose="proof")

    # Plant a match row that references this SHA in its proof_json.
    proof = {"amount": 1, "currency": "USD", "source_file": "keep.png",
             "source_sha256": sha}
    db.save_session("keep-sess", "Maybank", {
        "summary": {"total_proofs": 1, "total_txns": 0, "matched": 1,
                    "soft_matches": 0, "unmatched_proofs": 0,
                    "unmatched_txns": 0},
        "trace": [],
        "matches": [{
            "proof": proof,
            "txn": {"id": "tx", "amount": 1, "currency": "USD",
                    "description": "x", "reference": ""},
            "conversion": {"fx_rate": 1, "expected_gross": 1, "expected_net": 1,
                           "actual_received": 1, "fee_pct": 0, "fee_amount": 0,
                           "provenance": {"proof_amount":
                                          {"source_sha256": sha}}},
            "confidence": 0.9, "reasoning": "x", "status": "matched",
        }],
        "soft_matches": [], "unmatched_proofs": [], "unmatched_txns": [],
    })

    # Age the upload.
    old_iso = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(timespec="seconds")
    with db.conn() as c:
        c.execute("UPDATE raw_uploads SET uploaded_at = ? WHERE id = ?",
                  (old_iso, rec["id"]))

    out = uploads.gc_old_uploads(older_than_days=30, keep_sessions=True)
    assert out["rows_skipped_still_referenced"] >= 1
    # Bytes still on disk
    assert Path(rec["storage_path"]).exists()
    assert db.find_upload_by_sha(sha) is not None
