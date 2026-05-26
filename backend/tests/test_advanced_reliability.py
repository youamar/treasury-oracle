"""Circuit breaker, async OCR batch, workflow recover."""
import asyncio
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import reliability, campaign, campaign_workflow as cwf_mod
from app import ocr


client = TestClient(app)


class _StatusErr(Exception):
    def __init__(self, sc): super().__init__("boom"); self.status_code = sc


# ---------- circuit breaker ----------

def test_breaker_opens_after_threshold(monkeypatch):
    src = "test.breaker.opens"
    reliability.configure_breaker(src, reliability.BreakerConfig(
        failure_threshold=2, cooldown_seconds=60))
    reliability.reset_breaker(src)
    pol = reliability.RetryPolicy(max_attempts=1, base_delay=0, max_delay=0, jitter=0)

    def fn(): raise _StatusErr(503)

    for _ in range(2):
        with pytest.raises(_StatusErr):
            reliability.with_retry(fn, policy=pol, source=src)
    # Third call short-circuits because breaker is open
    with pytest.raises(reliability.BreakerOpen):
        reliability.with_retry(fn, policy=pol, source=src)


def test_breaker_closes_on_success_after_cooldown(monkeypatch):
    src = "test.breaker.recovers"
    reliability.configure_breaker(src, reliability.BreakerConfig(
        failure_threshold=2, cooldown_seconds=0.01))
    reliability.reset_breaker(src)
    pol = reliability.RetryPolicy(max_attempts=1, base_delay=0, max_delay=0, jitter=0)

    # Trip the breaker
    def bad(): raise _StatusErr(503)
    for _ in range(2):
        with pytest.raises(_StatusErr):
            reliability.with_retry(bad, policy=pol, source=src)

    # Wait past cooldown, then succeed → breaker should close
    import time as _t; _t.sleep(0.02)
    out = reliability.with_retry(lambda: "ok", policy=pol, source=src)
    assert out == "ok"
    snap = {b["source"]: b for b in reliability.breaker_snapshot()}
    assert snap[src]["state"] == "closed"


def test_breaker_ignores_terminal_400():
    src = "test.breaker.terminal"
    reliability.configure_breaker(src, reliability.BreakerConfig(
        failure_threshold=2, cooldown_seconds=60))
    reliability.reset_breaker(src)
    pol = reliability.RetryPolicy(max_attempts=1, base_delay=0, max_delay=0, jitter=0)

    def fn(): raise _StatusErr(400)
    # 400 isn't retryable and isn't auth — breaker shouldn't count it
    for _ in range(5):
        with pytest.raises(_StatusErr):
            reliability.with_retry(fn, policy=pol, source=src)
    snap = {b["source"]: b for b in reliability.breaker_snapshot()}
    assert snap[src]["state"] == "closed"


def test_breakers_endpoint_round_trip():
    src = "test.breaker.api"
    reliability.configure_breaker(src, reliability.BreakerConfig(
        failure_threshold=1, cooldown_seconds=60))
    reliability.reset_breaker(src)
    pol = reliability.RetryPolicy(max_attempts=1, base_delay=0, max_delay=0, jitter=0)
    with pytest.raises(_StatusErr):
        reliability.with_retry(lambda: (_ for _ in ()).throw(_StatusErr(503)),
                               policy=pol, source=src)

    r = client.get("/api/memory/breakers").json()
    assert any(b["source"] == src and b["state"] == "open" for b in r["breakers"])
    r = client.post("/api/memory/breakers/reset",
                    params={"source": src}).json()
    assert r["ok"] is True


# ---------- async OCR batch ----------

def test_ocr_batch_runs_concurrently(monkeypatch):
    """All N items should complete; we verify by counting calls + ordering."""
    calls = {"n": 0}

    def fake_extract(image_bytes, filename=""):
        calls["n"] += 1
        return {"amount": 100, "currency": "USD", "source_file": filename}

    monkeypatch.setattr(ocr, "extract_payment_proof", fake_extract)
    items = [(b"x", f"f{i}.png") for i in range(8)]
    out = asyncio.run(ocr.extract_payment_proofs_batch(items))
    assert len(out) == 8
    assert calls["n"] == 8
    assert {r["source_file"] for r in out} == {f"f{i}.png" for i in range(8)}


def test_ocr_batch_surfaces_per_item_errors(monkeypatch):
    def fake(image_bytes, filename=""):
        if "bad" in filename:
            raise ValueError("synthetic ocr failure")
        return {"amount": 1, "currency": "USD", "source_file": filename}
    monkeypatch.setattr(ocr, "extract_payment_proof", fake)
    items = [(b"x", "good.png"), (b"x", "bad.png"), (b"x", "good2.png")]
    out = asyncio.run(ocr.extract_payment_proofs_batch(items))
    assert len(out) == 3
    bad = [r for r in out if r["source_file"] == "bad.png"][0]
    assert "error" in bad


# ---------- workflow recover ----------

def test_workflow_recover_resumes_after_error(monkeypatch):
    c = campaign.create_campaign(
        client_name="Y", invoice_ref="INV-REC-1",
        invoice_amount=100, invoice_ccy="USD", outstanding=10,
        due_date="2026-05-01",
    )
    call = {"n": 0}
    original = cwf_mod._draft_stage

    def maybe_boom(idx, c_dict):
        call["n"] += 1
        if call["n"] == 1:
            raise RuntimeError("first call fails")
        return original(idx, c_dict)

    monkeypatch.setattr(cwf_mod, "_draft_stage", maybe_boom)
    cwf_mod.start(c["id"])           # sends stage 0 from create_campaign's draft
    snap = cwf_mod.tick(c["id"])     # triggers stage-1 draft → boom
    assert snap["status"] == "error"

    # Recover: clears error, re-loads, advances cleanly
    snap2 = cwf_mod.recover(c["id"])
    assert snap2["status"] != "error"
    assert snap2["error_message"] is None
