"""Boss documentary mode — epic 3-paragraph macro narrative for FX shortfalls."""
import json
from .chutes_client import chat, extract_content, strip_code_fences
from .config import REASONING_MODEL
from .reliability import ONE_SHOT_POLICY


DOC_PROMPT = """You are writing the voice-over script for a 60-second corporate documentary
explaining to a non-technical SME owner why this month's foreign-currency revenue came in
below expectation. The accountant is NOT at fault. Frame it like a David-Attenborough-meets-
Bloomberg narration — calm, authoritative, slightly grandiose, sweeping references to
macro forces. End on a reassuring note.

Three paragraphs. ~50 words each. Plain text in each paragraph (no markdown).

Context:
- Currency pair: {from_ccy} → {to_ccy}
- Invoice date: {invoice_date}, rate then: {rate_invoice}
- Payment date: {payment_date}, rate then: {rate_payment}
- Movement: {move_pct:+.2f}%
- Shortfall: {diff_local} {to_ccy} on a {amount} {from_ccy} invoice

Return JSON ONLY:
{{
  "title": "...",
  "paragraphs": ["...", "...", "..."],
  "tldr_for_boss": "<one calming sentence under 25 words>"
}}"""


def documentary_narrative(amount, from_ccy, to_ccy, invoice_date, payment_date,
                          rate_invoice, rate_payment, diff_local) -> dict:
    move_pct = ((rate_payment - rate_invoice) / rate_invoice * 100) if rate_invoice else 0
    prompt = DOC_PROMPT.format(
        from_ccy=from_ccy, to_ccy=to_ccy, invoice_date=invoice_date,
        payment_date=payment_date, rate_invoice=rate_invoice,
        rate_payment=rate_payment, move_pct=move_pct, diff_local=diff_local,
        amount=amount,
    )
    try:
        resp = chat(
            messages=[{"role": "user", "content": prompt}],
            model=REASONING_MODEL, temperature=0.7, max_tokens=2000,
            response_format={"type": "json_object"},
            timeout=60, retry_policy=ONE_SHOT_POLICY,
        )
        raw = extract_content(resp).strip()
        raw = strip_code_fences(raw)
        d = json.loads(raw)
    except Exception:
        d = {
            "title": f"The Tides of the {from_ccy} Market",
            "paragraphs": [
                f"In the great currents of global finance, the {from_ccy} ebbed and flowed "
                f"between {invoice_date} and {payment_date}, moving {move_pct:+.2f}% against us.",
                "Central banks, commodity shocks, and capital flows quietly rewrote our books "
                "while we slept. None of this originated in our accounting department.",
                f"The variance of {diff_local} {to_ccy} is the price of operating across borders. "
                "Hedging instruments exist, and we can evaluate them next quarter.",
            ],
            "tldr_for_boss":
                f"The {from_ccy} moved {move_pct:+.2f}%. The shortfall is FX, not bookkeeping.",
        }
    d["move_pct"] = round(move_pct, 4)
    return d
