"""Auto-dunning email generator + 'blame the Fed' boss chart data.

Both call the Chutes LLM; both return structured JSON the frontend renders.
"""
import json
from .chutes_client import chat
from .config import REASONING_MODEL
from .tools import get_fx_rate


LANG_MAP = {
    "USD": "English", "GBP": "English", "SGD": "English", "AUD": "English",
    "EUR": "English",  # safest default for EU multi-country
    "JPY": "Japanese", "CNY": "Chinese (Simplified)", "HKD": "Chinese (Traditional)",
    "KRW": "Korean", "THB": "Thai", "VND": "Vietnamese", "IDR": "Indonesian",
    "MYR": "English",
}


DUNNING_PROMPT = """You are a polite, high-EQ accounts-receivable specialist for a Malaysian SME.

Write a short collection email (max 120 words) to a client who underpaid an invoice.
Tone: warm, professional, never accusatory. Frame the shortfall as likely caused by
intermediary bank fees, not the client's fault. End with a clear, easy next step.

Write the email in: {language}

Invoice details:
- Invoice ref: {invoice_ref}
- Original amount: {invoice_amount} {invoice_ccy}
- Amount we received (after conversion): {received_local} {local_ccy}
- Shortfall (in invoice currency, approx.): {shortfall_invoice} {invoice_ccy}
- Client: {client_name}

Return JSON only:
{{
  "subject": "...",
  "body": "...",
  "language": "{language}"
}}"""


def generate_dunning(
    client_name: str,
    invoice_ref: str,
    invoice_amount: float,
    invoice_ccy: str,
    received_local: float,
    local_ccy: str,
    shortfall_invoice: float,
) -> dict:
    language = LANG_MAP.get(invoice_ccy.upper(), "English")
    prompt = DUNNING_PROMPT.format(
        language=language,
        invoice_ref=invoice_ref or "(no ref)",
        invoice_amount=invoice_amount,
        invoice_ccy=invoice_ccy,
        received_local=received_local,
        local_ccy=local_ccy,
        shortfall_invoice=shortfall_invoice,
        client_name=client_name or "Valued Client",
    )
    try:
        resp = chat(
            messages=[{"role": "user", "content": prompt}],
            model=REASONING_MODEL,
            temperature=0.5,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        data = json.loads(raw)
        data.setdefault("language", language)
        return data
    except Exception as e:
        return {
            "subject": f"Gentle reminder — Invoice {invoice_ref}",
            "body": (
                f"Dear {client_name},\n\nThank you for your recent payment for "
                f"invoice {invoice_ref}. After intermediary bank fees, we received "
                f"{received_local} {local_ccy} — about {shortfall_invoice} "
                f"{invoice_ccy} short of the invoiced amount. Would you kindly "
                f"arrange the small difference at your convenience? Happy to share "
                f"the full transaction breakdown if helpful.\n\nWarm regards"
            ),
            "language": language,
            "fallback": True,
            "error": str(e),
        }


# --- Boss chart -------------------------------------------------------------

BOSS_EXPLAINER_PROMPT = """You are explaining an FX-driven shortfall to a non-technical SME owner.
In ONE sentence (under 30 words), explain why the company received less local currency
than the invoice value. Reference the rate movement specifically. Tone: calm, factual,
slightly reassuring. The accountant did nothing wrong.

Data:
- Invoice: {amount} {from_ccy} on {invoice_date}
- FX rate on invoice date ({from_ccy}->{to_ccy}): {rate_invoice}
- FX rate on payment date: {rate_payment}
- Result: received {actual_local} {to_ccy} vs expected {expected_local} {to_ccy}

Return JSON: {{ "headline": "...", "explanation": "..." }}"""


def boss_chart(
    amount: float,
    from_ccy: str,
    to_ccy: str,
    invoice_date: str,
    payment_date: str,
    actual_local: float,
) -> dict:
    rate_inv = get_fx_rate(from_ccy, to_ccy, invoice_date)
    rate_pay = get_fx_rate(from_ccy, to_ccy, payment_date)
    expected_local = round(amount * rate_inv, 2)
    move_pct = ((rate_pay - rate_inv) / rate_inv * 100) if rate_inv else 0

    explainer = {"headline": "FX movement explainer", "explanation": ""}
    try:
        prompt = BOSS_EXPLAINER_PROMPT.format(
            amount=amount, from_ccy=from_ccy, to_ccy=to_ccy,
            invoice_date=invoice_date, rate_invoice=rate_inv,
            rate_payment=rate_pay, actual_local=actual_local,
            expected_local=expected_local,
        )
        resp = chat(
            messages=[{"role": "user", "content": prompt}],
            model=REASONING_MODEL,
            temperature=0.3,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        explainer = json.loads(raw)
    except Exception:
        explainer = {
            "headline": f"FX moved {move_pct:+.2f}% against us",
            "explanation": (
                f"On {invoice_date}, 1 {from_ccy} = {rate_inv:.4f} {to_ccy}; "
                f"by {payment_date} it had moved to {rate_pay:.4f}. "
                f"Currency markets, not bookkeeping, account for the difference."
            ),
        }

    return {
        "invoice_date": invoice_date,
        "payment_date": payment_date,
        "rate_invoice_date": rate_inv,
        "rate_payment_date": rate_pay,
        "rate_move_pct": round(move_pct, 4),
        "expected_local": expected_local,
        "actual_local": actual_local,
        "diff_local": round(expected_local - actual_local, 2),
        "from_ccy": from_ccy.upper(),
        "to_ccy": to_ccy.upper(),
        **explainer,
    }
