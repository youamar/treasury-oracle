"""LangGraph-backed autonomous campaign workflow."""
from fastapi.testclient import TestClient

from app.main import app
from app import campaign, campaign_workflow as cwf, db


client = TestClient(app)


def _new_campaign():
    return campaign.create_campaign(
        client_name="Acme Corp",
        invoice_ref="INV-WF-001",
        invoice_amount=1000.0,
        invoice_ccy="USD",
        outstanding=120.0,
        due_date="2026-05-01",
    )


def test_start_creates_thread_and_advances_one_stage():
    c = _new_campaign()
    snap = cwf.start(c["id"])
    assert snap["campaign_id"] == c["id"]
    assert snap["last_action"] == "sent_stage_0"
    assert snap["current_stage"] == 1
    assert "wait_for_response" in snap["interrupted_before"]


def test_tick_resumes_and_advances():
    c = _new_campaign()
    cwf.start(c["id"])
    snap = cwf.tick(c["id"])
    assert snap["current_stage"] == 2
    assert snap["last_action"] == "sent_stage_1"


def test_get_state_returns_persisted_snapshot():
    c = _new_campaign()
    cwf.start(c["id"])
    snap = cwf.get_state(c["id"])
    assert snap is not None
    assert snap["campaign_id"] == c["id"]


def test_tick_drives_to_exhaustion():
    c = _new_campaign()
    cwf.start(c["id"])
    # 4 stages total; one already sent by start. Tick until done.
    for _ in range(10):
        snap = cwf.tick(c["id"])
        if snap["done"]:
            break
    assert snap["done"] is True
    db_c = db.get_campaign(c["id"])
    assert db_c["status"] == "exhausted"


def test_paid_short_circuits_finalize():
    c = _new_campaign()
    cwf.start(c["id"])
    # mark paid out-of-band
    paid = db.get_campaign(c["id"])
    paid["status"] = "paid"
    db.upsert_campaign(paid)
    snap = cwf.tick(c["id"])
    assert snap["done"] is True
    assert snap["last_action"] == "finalized"


def test_workflow_endpoints_round_trip():
    c = _new_campaign()
    r = client.post(f"/api/campaign/{c['id']}/workflow/start")
    assert r.status_code == 200, r.text
    assert r.json()["current_stage"] == 1

    r = client.get(f"/api/campaign/{c['id']}/workflow/state")
    assert r.status_code == 200
    assert r.json()["campaign_id"] == c["id"]

    r = client.post(f"/api/campaign/{c['id']}/workflow/tick")
    assert r.status_code == 200
    assert r.json()["current_stage"] == 2


def test_workflow_404_on_unknown_campaign():
    r = client.post("/api/campaign/does_not_exist/workflow/start")
    assert r.status_code == 404
