from app.fx_history import get_fx_series, peak_analysis, what_if, watcher_check


def _force_offline(monkeypatch):
    import httpx
    def boom(*a, **kw): raise httpx.ConnectError("offline")
    monkeypatch.setattr(httpx, "get", boom)
    get_fx_series.cache_clear()


def test_series_returns_data_offline(monkeypatch):
    _force_offline(monkeypatch)
    series = get_fx_series("USD", "MYR", 10)
    assert len(series) >= 10
    assert all("date" in s and "rate" in s for s in series)


def test_peak_analysis(monkeypatch):
    _force_offline(monkeypatch)
    p = peak_analysis("USD", "MYR", 30)
    assert p["peak"]["rate"] >= p["average"] >= p["trough"]["rate"]
    assert p["spread_pct"] >= 0


def test_what_if(monkeypatch):
    _force_offline(monkeypatch)
    w = what_if(10000, "USD", "MYR", 30)
    assert w["at_peak"] >= w["at_average"] >= w["at_trough"]
    assert w["missed_profit_vs_avg"] >= 0


def test_watcher_check_below_target(monkeypatch):
    _force_offline(monkeypatch)
    c = watcher_check("USD", "MYR", target_rate=999.0)
    assert c["hit"] is False


def test_watcher_check_above_target(monkeypatch):
    _force_offline(monkeypatch)
    c = watcher_check("USD", "MYR", target_rate=0.01)
    assert c["hit"] is True
