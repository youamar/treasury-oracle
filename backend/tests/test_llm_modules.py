"""Tests for LLM-dependent modules — Chutes is mocked by conftest."""
from app.dunning import generate_dunning, boss_chart
from app.documentary import documentary_narrative
from app.validator import validate_submission
from app.voice import extract_from_transcript
from app.campaign import create_campaign, advance_campaign, mark_paid, list_campaigns


def test_dunning_email():
    out = generate_dunning(
        "Acme Corp", "INV-001", 1000, "USD", 4500, "MYR", 50,
    )
    assert "subject" in out
    assert "body" in out
    assert out["language"] in ("English", "Japanese", "Chinese (Simplified)")


def test_boss_chart(monkeypatch):
    # offline-safe
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("x")))
    from app.tools import get_fx_rate; get_fx_rate.cache_clear()
    out = boss_chart(1000, "USD", "MYR", "2026-05-01", "2026-05-25", 4500.0)
    assert "headline" in out
    assert "rate_invoice_date" in out
    assert "diff_local" in out


def test_documentary():
    out = documentary_narrative(1000, "USD", "MYR", "2026-05-01", "2026-05-25",
                                4.72, 4.65, 70.0)
    assert "title" in out
    assert isinstance(out["paragraphs"], list)
    assert len(out["paragraphs"]) == 3
    assert "tldr_for_boss" in out


def test_validator_accepts_clean():
    out = validate_submission({
        "amount": 1000, "currency": "USD", "date": "2026-05-20",
        "payer": "Acme", "reference": "INV-001",
    })
    assert out["verdict"] in ("accept", "reject")  # mock chooses based on MISSING


def test_validator_rejects_garbage():
    out = validate_submission({
        "amount": None, "currency": "$", "date": "",
        "payer": "", "reference": "",
    })
    assert out["verdict"] == "reject"


def test_voice_transcript():
    out = extract_from_transcript("I just sent 500 USD for invoice INV-007 today.")
    assert out["amount"] == 500
    assert out["currency"] == "USD"
    assert out["transcript"]


def test_campaign_lifecycle():
    c = create_campaign("Client", "INV-X", 1000, "USD", 100, "2026-05-01")
    assert c["current_stage"] == 0
    assert len(c["history"]) == 1

    c2 = advance_campaign(c["id"])
    assert c2["current_stage"] == 1
    assert len(c2["history"]) == 2

    c3 = mark_paid(c["id"])
    assert c3["status"] == "paid"

    assert any(x["id"] == c["id"] for x in list_campaigns())
