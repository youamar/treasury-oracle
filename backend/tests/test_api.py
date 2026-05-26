"""End-to-end integration tests against the FastAPI app (TestClient)."""
import io
import pandas as pd
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_extract_proofs_endpoint():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(buf, format="PNG")
    r = client.post("/api/extract-proofs",
                    files=[("files", ("p.png", buf.getvalue(), "image/png"))])
    assert r.status_code == 200
    proofs = r.json()["proofs"]
    assert proofs and proofs[0].get("amount") and "error" not in proofs[0]


def test_parse_statement_endpoint():
    df = pd.DataFrame(
        [["2026-05-20", 4700.0, "MYR", "TEST", "INV-001"]],
        columns=["Date", "Amount", "Currency", "Description", "Reference"],
    )
    buf = io.BytesIO(); df.to_csv(buf, index=False)
    r = client.post("/api/parse-statement",
                    files={"file": ("s.csv", buf.getvalue(), "text/csv")})
    assert r.status_code == 200
    assert len(r.json()["transactions"]) == 1


def test_reconcile_endpoint(sample_proof, sample_txn):
    # Use classical mode in this integration test so we don't depend on the
    # full agent tool-call scripting (agent mode is covered in test_agent.py).
    r = client.post("/api/reconcile", json={
        "proofs": [sample_proof], "transactions": [sample_txn],
        "bank": "Maybank", "mode": "classical",
    })
    assert r.status_code == 200
    j = r.json()
    assert "recon_id" in j
    assert j["summary"]["matched"] == 1

    # Report download
    rep = client.get(f"/api/report/{j['recon_id']}")
    assert rep.status_code == 200
    assert rep.content.startswith(b"%PDF-")

    # Audit pack
    ap = client.get(f"/api/audit-pack/{j['recon_id']}/0")
    assert ap.status_code == 200
    assert ap.content.startswith(b"%PDF-")


def test_dunning_endpoint():
    r = client.post("/api/dunning", json={
        "client_name": "Acme", "invoice_ref": "INV-1",
        "invoice_amount": 1000, "invoice_ccy": "USD",
        "received_local": 4500, "local_ccy": "MYR", "shortfall_invoice": 50,
    })
    assert r.status_code == 200 and "body" in r.json()


def test_campaign_endpoints():
    r = client.post("/api/campaign", json={
        "client_name": "X", "invoice_ref": "INV-X",
        "invoice_amount": 1000, "invoice_ccy": "USD", "outstanding": 100,
    })
    assert r.status_code == 200
    cid = r.json()["id"]

    adv = client.post(f"/api/campaign/{cid}/advance")
    assert adv.status_code == 200 and adv.json()["current_stage"] == 1

    paid = client.post(f"/api/campaign/{cid}/paid")
    assert paid.status_code == 200 and paid.json()["status"] == "paid"


def test_sales_validate_endpoint():
    r = client.post("/api/sales/validate", json={
        "amount": None, "currency": "$", "date": "", "payer": "", "reference": "",
    })
    assert r.status_code == 200 and r.json()["verdict"] == "reject"


def test_fx_what_if_endpoint(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("x")))
    from app.fx_history import get_fx_series; get_fx_series.cache_clear()
    r = client.get("/api/fx/what-if?amount=1000&from_ccy=USD&to_ccy=MYR&days=10")
    assert r.status_code == 200
    j = r.json()
    assert j["at_peak"] >= j["at_average"]


def test_inbox_poll():
    r = client.get("/api/inbox/poll")
    assert r.status_code == 200 and "items" in r.json()


def test_voice_transcript_endpoint():
    r = client.post("/api/voice", data={"transcript": "Sent 500 USD for INV-007."})
    assert r.status_code == 200
    assert r.json()["amount"] == 500


def test_reconcile_idempotency_returns_cached_session():
    """F8: same Idempotency-Key + same body returns the cached recon_id,
    no second LLM call."""
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)

    body = {
        "proofs": [{"amount": 1000.0, "currency": "USD", "date": "2026-05-20",
                    "payer": "Acme", "payee": "BT", "reference": "INV-IDEM",
                    "source_file": "p.png"}],
        "transactions": [{"id": "txn_0", "date": "2026-05-20",
                          "amount": 4696.40, "currency": "MYR",
                          "description": "INWARD", "reference": "INV-IDEM",
                          "direction": "in"}],
        "bank": "Maybank",
        "mode": "classical",  # deterministic, no LLM noise
    }
    r1 = client.post("/api/reconcile", json=body,
                     headers={"Idempotency-Key": "test-key-1"})
    assert r1.status_code == 200
    j1 = r1.json()
    rid1 = j1["recon_id"]
    assert "idempotent_replay" not in j1

    r2 = client.post("/api/reconcile", json=body,
                     headers={"Idempotency-Key": "test-key-1"})
    assert r2.status_code == 200
    j2 = r2.json()
    assert j2["recon_id"] == rid1
    assert j2.get("idempotent_replay") is True


def test_reconcile_idempotency_conflict_on_different_body():
    """Same key + different body → 409 conflict (Stripe-style)."""
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)

    body_a = {
        "proofs": [{"amount": 1000.0, "currency": "USD", "date": "2026-05-20",
                    "source_file": "a.png"}],
        "transactions": [{"id": "txn_0", "date": "2026-05-20", "amount": 4700,
                          "currency": "MYR", "direction": "in"}],
        "bank": "Maybank", "mode": "classical",
    }
    body_b = {**body_a, "bank": "CIMB"}

    r1 = client.post("/api/reconcile", json=body_a,
                     headers={"Idempotency-Key": "conflict-key"})
    assert r1.status_code == 200
    r2 = client.post("/api/reconcile", json=body_b,
                     headers={"Idempotency-Key": "conflict-key"})
    assert r2.status_code == 409


def test_reconcile_no_key_runs_every_time():
    """Without an Idempotency-Key, each call produces a fresh recon_id."""
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)

    body = {
        "proofs": [{"amount": 1000.0, "currency": "USD", "date": "2026-05-20",
                    "source_file": "p.png"}],
        "transactions": [{"id": "txn_0", "date": "2026-05-20", "amount": 4700,
                          "currency": "MYR", "direction": "in"}],
        "bank": "Maybank", "mode": "classical",
    }
    r1 = client.post("/api/reconcile", json=body).json()
    r2 = client.post("/api/reconcile", json=body).json()
    assert r1["recon_id"] != r2["recon_id"]


def test_rate_limit_returns_429_when_exceeded():
    """R-5: 11th eval/run call within 60s gets a 429 with Retry-After."""
    from fastapi.testclient import TestClient
    from app.main import app, _rate_windows
    _rate_windows.clear()  # isolate test from any prior limit state

    client = TestClient(app)
    # 10 calls allowed per minute for /api/eval/run per the config.
    for _ in range(10):
        r = client.post("/api/eval/run", json={"label": "rl-test"})
        assert r.status_code == 200
    r = client.post("/api/eval/run", json={"label": "rl-bust"})
    assert r.status_code == 429
    assert "retry_after_seconds" in r.json()
    assert r.headers.get("Retry-After")


def test_rate_limit_scoped_per_tenant():
    """Different tenants don't share the same bucket."""
    from fastapi.testclient import TestClient
    from app.main import app, _rate_windows
    _rate_windows.clear()

    client = TestClient(app)
    for _ in range(10):
        r = client.post("/api/eval/run", json={},
                        headers={"x-tenant-id": "tenant-a"})
        assert r.status_code == 200
    # Tenant A is now rate-limited
    r1 = client.post("/api/eval/run", json={},
                     headers={"x-tenant-id": "tenant-a"})
    assert r1.status_code == 429
    # Tenant B still has a clean bucket
    r2 = client.post("/api/eval/run", json={},
                     headers={"x-tenant-id": "tenant-b"})
    assert r2.status_code == 200


def test_banks_seeded_per_tenant_and_editable():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    # Fresh tenant gets the historical BANK_FEES seed.
    r = client.get("/api/banks", headers={"x-tenant-id": "banks-test"})
    assert r.status_code == 200
    banks = r.json()["banks"]
    ids = {b["id"] for b in banks}
    assert {"Maybank", "CIMB", "default"} <= ids
    maybank = next(b for b in banks if b["id"] == "Maybank")
    assert maybank["inbound_fee_pct"] == 0.005

    # Edit Maybank's fee — should round-trip.
    r2 = client.put("/api/banks/Maybank",
                    json={"id": "Maybank", "name": "Maybank (custom)",
                          "inbound_fee_pct": 0.006},
                    headers={"x-tenant-id": "banks-test"})
    assert r2.status_code == 200
    assert r2.json()["inbound_fee_pct"] == 0.006

    # Other tenants don't see the edit.
    r3 = client.get("/api/banks", headers={"x-tenant-id": "banks-other"})
    other = next(b for b in r3.json()["banks"] if b["id"] == "Maybank")
    assert other["inbound_fee_pct"] == 0.005


def test_apply_bank_fee_reads_from_db():
    """tools.apply_bank_fee uses the per-tenant DB value when available."""
    from app import db, tools
    with db.tenant_scope("fee-db-test"):
        db.upsert_bank({"id": "MyBank", "name": "MyBank",
                        "inbound_fee_pct": 0.012})
        result = tools.apply_bank_fee(1000.0, "MyBank")
        assert result["fee_pct"] == 0.012
        assert result["source"].startswith("db:banks/")
