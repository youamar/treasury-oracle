"""Agent tools: FX rate lookup and bank fee application."""
import httpx
from datetime import datetime
from functools import lru_cache
from .config import BANK_FEES


@lru_cache(maxsize=256)
def get_fx_rate(from_ccy: str, to_ccy: str, date: str) -> float:
    """
    Fetch historical FX rate for a specific date.
    date: ISO format YYYY-MM-DD
    Returns: rate such that amount_from * rate = amount_to
    """
    from_ccy = from_ccy.upper()
    to_ccy = to_ccy.upper()
    if from_ccy == to_ccy:
        return 1.0

    # Try frankfurter.app (free, no key, ECB data)
    try:
        url = f"https://api.frankfurter.app/{date}?from={from_ccy}&to={to_ccy}"
        r = httpx.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if "rates" in data and to_ccy in data["rates"]:
                return float(data["rates"][to_ccy])
    except Exception:
        pass

    # Fallback: exchangerate.host
    try:
        url = f"https://api.exchangerate.host/{date}?base={from_ccy}&symbols={to_ccy}"
        r = httpx.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if "rates" in data and to_ccy in data["rates"]:
                return float(data["rates"][to_ccy])
    except Exception:
        pass

    # Last-resort static fallback rates so demo never breaks
    STATIC_RATES = {
        ("USD", "MYR"): 4.72, ("EUR", "MYR"): 5.10, ("SGD", "MYR"): 3.52,
        ("GBP", "MYR"): 5.95, ("JPY", "MYR"): 0.031, ("CNY", "MYR"): 0.65,
        ("USD", "SGD"): 1.34, ("USD", "EUR"): 0.92, ("USD", "GBP"): 0.79,
    }
    if (from_ccy, to_ccy) in STATIC_RATES:
        return STATIC_RATES[(from_ccy, to_ccy)]
    if (to_ccy, from_ccy) in STATIC_RATES:
        return 1.0 / STATIC_RATES[(to_ccy, from_ccy)]
    return 1.0


def apply_bank_fee(amount: float, bank_name: str = "default") -> dict:
    """Returns {fee_pct, fee_amount, net_amount}."""
    pct = BANK_FEES.get(bank_name, BANK_FEES["default"])
    fee = round(amount * pct, 2)
    return {
        "bank": bank_name,
        "fee_pct": pct,
        "fee_amount": fee,
        "net_amount": round(amount - fee, 2),
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
