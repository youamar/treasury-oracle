from app.tools import get_fx_rate, apply_bank_fee, convert_currency, dispatch_tool


def test_fx_same_currency():
    assert get_fx_rate("USD", "USD", "2026-05-20") == 1.0


def test_fx_static_fallback(monkeypatch):
    # Force the external calls to fail so we exercise the static table
    import httpx
    def boom(*a, **kw): raise httpx.ConnectError("offline")
    monkeypatch.setattr(httpx, "get", boom)
    get_fx_rate.cache_clear()
    rate = get_fx_rate("USD", "MYR", "2026-05-20")
    assert 4.0 < rate < 5.5


def test_bank_fee_known_bank():
    info = apply_bank_fee(1000.0, "Maybank")
    assert info["fee_pct"] == 0.005
    assert info["fee_amount"] == 5.0
    assert info["net_amount"] == 995.0


def test_bank_fee_unknown_bank_uses_default():
    info = apply_bank_fee(1000.0, "Imaginary Bank")
    assert info["fee_pct"] == 0.005


def test_convert_currency_end_to_end():
    res = convert_currency(100, "USD", "USD", "2026-05-20", "Maybank")
    assert res["fx_rate"] == 1.0
    assert res["net_amount"] == 99.5


def test_dispatch_tool_unknown():
    res = dispatch_tool("nonexistent", {})
    assert "error" in res


def test_fx_fallback_uses_db_value(monkeypatch):
    """C-6: DB-backed fallback rate overrides the in-code static dict
    when both live providers fail."""
    import httpx
    from app import tools, db
    # Force live providers to fail so we fall through to fallback.
    def boom(*a, **kw): raise httpx.ConnectError("offline")
    monkeypatch.setattr(httpx, "get", boom)
    tools.get_fx_rate.cache_clear()
    tools._fx_lookup_cached.cache_clear()

    with db.tenant_scope("fx-db-test"):
        db.upsert_fx_fallback_rate("USD", "MYR", 99.99)
        out = tools.get_fx_rate_full("USD", "MYR", "2026-05-26")
        assert out["rate"] == 99.99
        assert out["source"] == "static_fallback"
        assert out["trusted"] is False


def test_fx_fallback_endpoints():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    r = client.get("/api/fx/fallback",
                   headers={"x-tenant-id": "fx-ep-test"})
    assert r.status_code == 200
    # Seeded with the historical defaults
    pairs = {(b["from_ccy"], b["to_ccy"]) for b in r.json()["rates"]}
    assert ("USD", "MYR") in pairs

    # Add a new pair
    r2 = client.put("/api/fx/fallback",
                    json={"from_ccy": "AUD", "to_ccy": "MYR", "rate": 3.15},
                    headers={"x-tenant-id": "fx-ep-test"})
    assert r2.status_code == 200
    assert r2.json()["rate"] == 3.15
