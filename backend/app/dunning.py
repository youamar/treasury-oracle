"""Auto-dunning email generator + 'blame the Fed' boss chart data.

Both call the Chutes LLM; both return structured JSON the frontend renders.
"""
import json
from .chutes_client import chat, extract_content, strip_code_fences
from .config import REASONING_MODEL
from .reliability import TWO_SHOT_POLICY
from .tools import get_fx_rate


LANG_MAP = {
    "USD": "English", "GBP": "English", "SGD": "English", "AUD": "English",
    "EUR": "English",  # safest default for EU multi-country
    "JPY": "Japanese", "CNY": "Chinese (Simplified)", "HKD": "Chinese (Traditional)",
    "KRW": "Korean", "THB": "Thai", "VND": "Vietnamese", "IDR": "Indonesian",
    "MYR": "English",
}


DUNNING_PROMPT_UNDERPAYMENT = """You are a polite, high-EQ accounts-receivable specialist for a Malaysian SME.

Write a short collection email (max 120 words) to a client whose payment came in
SHORT of the invoiced amount. Tone: warm, professional, never accusatory. Frame
the shortfall as likely caused by intermediary bank fees, not the client's fault.
End with a clear, easy next step.

Write the email in: {language}

Invoice details:
- Invoice ref: {invoice_ref}
- Original amount: {invoice_amount} {invoice_ccy}
- Amount we actually received (after conversion): {received_local} {local_ccy}
- Shortfall (in invoice currency, approx.): {shortfall_invoice} {invoice_ccy}
- Client: {client_name}

Hard rules for the response:
- Return ONE JSON object only — no commentary, no markdown fences.
- The "body" field is a single JSON string. Use real newline characters
  (encoded \\n in JSON) between paragraphs. Do NOT emit \\\\n.
- Do NOT include placeholders like "[Your Name]" or "[Company Name]".
  Sign off with just "Warm regards," on its own line.

Return JSON exactly in this shape:
{{
  "subject": "...",
  "body": "...",
  "language": "{language}"
}}"""


DUNNING_PROMPT_NONPAYMENT = """You are a polite, high-EQ accounts-receivable specialist for a Malaysian SME.

Write a short follow-up email (max 120 words) about an invoice we have NOT
received any payment for yet. Do NOT mention "shortfall" or "received amount" —
no money has arrived. Tone: warm, assume good faith (perhaps the wire is in
transit or stuck at a correspondent bank), never accusatory. End with a clear,
easy next step: ask the client to confirm payment status or share a remittance
reference.

Write the email in: {language}

Invoice details:
- Invoice ref: {invoice_ref}
- Original amount: {invoice_amount} {invoice_ccy}
- Client: {client_name}

Hard rules for the response:
- Return ONE JSON object only — no commentary, no markdown fences.
- The "body" field is a single JSON string. Use real newline characters
  (encoded \\n in JSON) between paragraphs. Do NOT emit \\\\n.
- Do NOT include placeholders like "[Your Name]" or "[Company Name]".
  Sign off with just "Warm regards," on its own line.

Return JSON exactly in this shape:
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
    # Branch on scenario — non-payment and underpayment need different
    # framing. Sending an underpayment email when nothing arrived reads
    # robotic ("we received 0 MYR…") and damages the client relationship.
    # Treat None (caller didn't pass the field) and explicit 0 the same:
    # both mean "no payment has been recorded for this invoice".
    try:
        rl = float(received_local) if received_local is not None else 0.0
    except (TypeError, ValueError):
        rl = 0.0
    is_nonpayment = rl <= 0
    if is_nonpayment:
        prompt = DUNNING_PROMPT_NONPAYMENT.format(
            language=language,
            invoice_ref=invoice_ref or "(no ref)",
            invoice_amount=invoice_amount,
            invoice_ccy=invoice_ccy,
            client_name=client_name or "Valued Client",
        )
    else:
        prompt = DUNNING_PROMPT_UNDERPAYMENT.format(
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
        # max_tokens must cover reasoning + answer. At 1000 the reasoning
        # ate the whole budget and `content` was emitted empty — diagnosed
        # by direct probe (see extract_content docstring). 3000 leaves
        # ~2200 for reasoning + ~800 for the email JSON, which matches
        # the model's actual usage profile on this prompt.
        resp = chat(
            messages=[{"role": "user", "content": prompt}],
            model=REASONING_MODEL,
            temperature=0.5,
            max_tokens=3000,
            response_format={"type": "json_object"},
            timeout=90,
            retry_policy=TWO_SHOT_POLICY,
        )
        raw = extract_content(resp).strip()
        if not raw:
            raise ValueError("LLM returned empty content and no reasoning_content")
        raw = strip_code_fences(raw)
        data = json.loads(raw)
        data.setdefault("language", language)
        # Safety net — some reasoning models double-escape newlines inside
        # `response_format=json_object`, producing `\\n` (backslash + n) in
        # the decoded string instead of an actual newline byte. The frontend
        # renders that as literal "\n" in the email body. Unescape if seen.
        body = data.get("body") or ""
        if isinstance(body, str) and "\\n" in body and "\n" not in body:
            data["body"] = body.replace("\\n", "\n").replace("\\t", "\t")
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
            max_tokens=2500,
            response_format={"type": "json_object"},
            timeout=90,
            retry_policy=TWO_SHOT_POLICY,
        )
        raw = extract_content(resp).strip()
        if not raw:
            raise ValueError("LLM returned empty content and no reasoning_content")
        raw = strip_code_fences(raw)
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
