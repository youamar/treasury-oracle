from app.swift import trace_route, ROUTES


def test_usd_route_structure():
    r = trace_route("USD", 1000, 4700.0, 4630.0, fx_rate=4.72, local_currency="MYR")
    assert r["source_currency"] == "USD"
    assert r["local_currency"] == "MYR"
    assert len(r["nodes"]) == 3
    assert r["nodes"][0]["type"] == "originator"
    assert r["nodes"][1]["type"] == "correspondent"
    assert r["nodes"][2]["type"] == "beneficiary"
    assert r["gap_local"] == 70.0


def test_jpy_uses_specific_route():
    r = trace_route("JPY", 120000, 3700.0, 3650.0, fx_rate=0.031)
    assert "Tokyo" in r["nodes"][1]["name"]


def test_unknown_currency_falls_back_to_usd_route():
    r = trace_route("ZZZ", 100, 400, 380, fx_rate=4.0)
    assert len(r["nodes"]) == 3


def test_explanation_present():
    r = trace_route("USD", 1000, 4700, 4630, 4.72)
    assert "explanation" in r
    assert isinstance(r["explanation"], str)
