"""Agent tools: FX rate lookup and bank fee application."""
import httpx
from datetime import date as _date, datetime
from functools import lru_cache
from .config import BANK_FEES


# Sources, ranked by trustworthiness. The agent must refuse strict-match on
# anything below "ecb_live" — see agent.py decision policy.
FX_SOURCE_ECB = "ecb_live"           # frankfurter.app (ECB)
FX_SOURCE_EXR = "exchangerate_host"  # secondary live source
FX_SOURCE_STATIC = "static_fallback" # baked-in table, stale
FX_SOURCE_IDENTITY = "identity_fallback"  # 1.0 — no data at all
FX_SOURCE_SAME = "same_currency"

_TRUSTED_LIVE_SOURCES = {FX_SOURCE_ECB, FX_SOURCE_EXR, FX_SOURCE_SAME}


_STATIC_RATES = {
    ("USD", "MYR"): 4.72, ("EUR", "MYR"): 5.10, ("SGD", "MYR"): 3.52,
    ("GBP", "MYR"): 5.95, ("JPY", "MYR"): 0.031, ("CNY", "MYR"): 0.65,
    ("USD", "SGD"): 1.34, ("USD", "EUR"): 0.92, ("USD", "GBP"): 0.79,
}


@lru_cache(maxsize=256)
def _fx_lookup_cached(from_ccy: str, to_ccy: str, date: str) -> tuple[float, str]:
    """Returns (rate, source). Cached for historical dates only — callers must
    bypass the cache for `date == today`."""
    if from_ccy == to_ccy:
        return 1.0, FX_SOURCE_SAME

    try:
        url = f"https://api.frankfurter.app/{date}?from={from_ccy}&to={to_ccy}"
        r = httpx.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if "rates" in data and to_ccy in data["rates"]:
                return float(data["rates"][to_ccy]), FX_SOURCE_ECB
    except Exception:
        pass

    try:
        url = f"https://api.exchangerate.host/{date}?base={from_ccy}&symbols={to_ccy}"
        r = httpx.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if "rates" in data and to_ccy in data["rates"]:
                return float(data["rates"][to_ccy]), FX_SOURCE_EXR
    except Exception:
        pass

    # DB-backed fallback rates take priority over the in-code static table —
    # customers can keep them fresh from the Settings UI. Untrusted either way.
    try:
        from . import db as _db
        rate = _db.get_fx_fallback_rate(from_ccy, to_ccy)
        if rate is not None:
            return rate, FX_SOURCE_STATIC
        inv = _db.get_fx_fallback_rate(to_ccy, from_ccy)
        if inv is not None and inv != 0:
            return 1.0 / inv, FX_SOURCE_STATIC
    except Exception:
        pass
    if (from_ccy, to_ccy) in _STATIC_RATES:
        return _STATIC_RATES[(from_ccy, to_ccy)], FX_SOURCE_STATIC
    if (to_ccy, from_ccy) in _STATIC_RATES:
        return 1.0 / _STATIC_RATES[(to_ccy, from_ccy)], FX_SOURCE_STATIC
    return 1.0, FX_SOURCE_IDENTITY


def get_fx_rate_full(from_ccy: str, to_ccy: str, date: str) -> dict:
    """Full provenance lookup. Returns:
        {rate, source, trusted, stale, asof}
    - `trusted`: safe for strict-match decisions
    - `stale`: rate may not reflect today's market (static fallback or cache-bypassed today)
    """
    from_ccy = from_ccy.upper()
    to_ccy = to_ccy.upper()
    today = _date.today().isoformat()
    if date == today:
        # Do NOT use lru_cache for today — rates move intraday.
        _fx_lookup_cached.cache_clear() if False else None  # no-op; we just bypass
        rate, source = _fx_lookup_uncached(from_ccy, to_ccy, date)
    else:
        rate, source = _fx_lookup_cached(from_ccy, to_ccy, date)
    return {
        "rate": rate,
        "source": source,
        "trusted": source in _TRUSTED_LIVE_SOURCES,
        "stale": source in {FX_SOURCE_STATIC, FX_SOURCE_IDENTITY},
        "asof": date,
    }


def _fx_lookup_uncached(from_ccy: str, to_ccy: str, date: str) -> tuple[float, str]:
    """Same lookup as cached version, but never cached. Used for today's date."""
    if from_ccy == to_ccy:
        return 1.0, FX_SOURCE_SAME
    try:
        url = f"https://api.frankfurter.app/{date}?from={from_ccy}&to={to_ccy}"
        r = httpx.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if "rates" in data and to_ccy in data["rates"]:
                return float(data["rates"][to_ccy]), FX_SOURCE_ECB
    except Exception:
        pass
    try:
        url = f"https://api.exchangerate.host/{date}?base={from_ccy}&symbols={to_ccy}"
        r = httpx.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if "rates" in data and to_ccy in data["rates"]:
                return float(data["rates"][to_ccy]), FX_SOURCE_EXR
    except Exception:
        pass
    # DB-backed fallback rates take priority over the in-code static table —
    # customers can keep them fresh from the Settings UI. Untrusted either way.
    try:
        from . import db as _db
        rate = _db.get_fx_fallback_rate(from_ccy, to_ccy)
        if rate is not None:
            return rate, FX_SOURCE_STATIC
        inv = _db.get_fx_fallback_rate(to_ccy, from_ccy)
        if inv is not None and inv != 0:
            return 1.0 / inv, FX_SOURCE_STATIC
    except Exception:
        pass
    if (from_ccy, to_ccy) in _STATIC_RATES:
        return _STATIC_RATES[(from_ccy, to_ccy)], FX_SOURCE_STATIC
    if (to_ccy, from_ccy) in _STATIC_RATES:
        return 1.0 / _STATIC_RATES[(to_ccy, from_ccy)], FX_SOURCE_STATIC
    return 1.0, FX_SOURCE_IDENTITY


def get_fx_rate(from_ccy: str, to_ccy: str, date: str) -> float:
    """Back-compat float wrapper. Prefer get_fx_rate_full for new callers so
    you get provenance and can refuse to strict-match on stale data."""
    return get_fx_rate_full(from_ccy, to_ccy, date)["rate"]


# Preserve the .cache_clear() API tests relied on before the cache moved
# into the underlying _fx_lookup_cached helper.
get_fx_rate.cache_clear = _fx_lookup_cached.cache_clear  # type: ignore[attr-defined]


def apply_bank_fee(amount: float, bank_name: str = "default") -> dict:
    """Returns {fee_pct, fee_amount, net_amount, source}.

    Looks up the per-tenant banks table first; falls back to the in-code
    BANK_FEES dict if the DB is unreachable or the tenant has no row yet.
    `source` tags which path was taken so the agent's provenance block
    can record it.
    """
    pct = None
    source = "config:BANK_FEES"
    try:
        from . import db as _db
        row = _db.get_bank(bank_name)
        if row and row.get("inbound_fee_pct") is not None:
            pct = float(row["inbound_fee_pct"])
            source = f"db:banks/{bank_name}"
    except Exception:
        # DB lookup failed (early-boot, missing tenant ctx, etc.) — use code defaults.
        pass
    if pct is None:
        pct = BANK_FEES.get(bank_name, BANK_FEES["default"])
    fee = round(amount * pct, 2)
    return {
        "bank": bank_name,
        "fee_pct": pct,
        "fee_amount": fee,
        "net_amount": round(amount - fee, 2),
        "source": source,
    }


def convert_currency(amount: float, from_ccy: str, to_ccy: str, date: str, bank: str = "default") -> dict:
    """End-to-end: convert + apply fee. Used by the matcher agent."""
    rate = get_fx_rate(from_ccy, to_ccy, date)
    gross = round(amount * rate, 2)
    fee_info = apply_bank_fee(gross, bank)
    return {
        "from_amount": amount,
        "from_currency": from_ccy,
        "to_currency": to_ccy,
        "fx_rate": rate,
        "fx_date": date,
        "gross_converted": gross,
        **fee_info,
    }


# Tool schemas for LLM function calling
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_fx_rate",
            "description": "Fetch historical foreign-exchange rate between two currencies on a specific date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_ccy": {"type": "string", "description": "ISO 4217 code, e.g. USD"},
                    "to_ccy": {"type": "string", "description": "ISO 4217 code, e.g. MYR"},
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                },
                "required": ["from_ccy", "to_ccy", "date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_bank_fee",
            "description": "Apply local bank's inbound-conversion fee to an amount.",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number"},
                    "bank_name": {"type": "string"},
                },
                "required": ["amount"],
            },
        },
    },
]


def dispatch_tool(name: str, args: dict):
    if name == "get_fx_rate":
        return {"rate": get_fx_rate(args["from_ccy"], args["to_ccy"], args["date"])}
    if name == "apply_bank_fee":
        return apply_bank_fee(args["amount"], args.get("bank_name", "default"))
    return {"error": f"unknown tool {name}"}
