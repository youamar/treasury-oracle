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
    r = client.post("/api/reconcile", json={
        "proofs": [sample_proof], "transactions": [sample_txn], "bank": "Maybank",
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
