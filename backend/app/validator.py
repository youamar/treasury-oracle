"""Sales submission validator — refuses sloppy proofs with a sassy LLM critique."""
import json
from .chutes_client import chat, extract_content, strip_code_fences
from .config import REASONING_MODEL
from .reliability import ONE_SHOT_POLICY

CRITIQUE_PROMPT = """You are a no-nonsense finance compliance bot. A salesperson just
submitted a payment proof. Audit its quality.

Submission metadata:
- amount: {amount}
- currency: {currency}
- date: {date}
- payer: {payer}
- reference: {reference}

Return JSON ONLY:
{{
  "verdict": "accept" | "reject",
  "issues": ["..."],          // empty if accept
  "severity": "ok"|"low"|"high",
  "message_to_sales": "..."   // <=40 words, tone: dry, slightly sarcastic but professional
}}

Reject if: currency is ambiguous like "$" without a code, amount missing, date missing,
or payer is "TBD"/"???"/empty. Otherwise accept."""


def validate_submission(proof: dict) -> dict:
    fields = {
        "amount": proof.get("amount"),
        "currency": proof.get("currency"),
        "date": proof.get("date"),
        "payer": proof.get("payer"),
        "reference": proof.get("reference"),
    }
    # Cheap pre-check
    obvious_fail = (
        fields["amount"] in (None, "", 0)
        or not fields["currency"] or fields["currency"] in ("$", "?", "??")
        or not fields["date"]
    )

    prompt = CRITIQUE_PROMPT.format(**{k: (v if v not in (None, "") else "MISSING")
                                       for k, v in fields.items()})
    try:
        resp = chat(
            messages=[{"role": "user", "content": prompt}],
            model=REASONING_MODEL, temperature=0.4, max_tokens=1200,
            response_format={"type": "json_object"},
            timeout=60, retry_policy=ONE_SHOT_POLICY,
        )
        raw = extract_content(resp).strip()
        raw = strip_code_fences(raw)
        return json.loads(raw)
    except Exception:
        if obvious_fail:
            return {
                "verdict": "reject", "severity": "high",
                "issues": ["amount/currency/date missing or ambiguous"],
                "message_to_sales": "Read the SOP. Try again, but this time use words and numbers.",
            }
        return {"verdict": "accept", "severity": "ok", "issues": [],
                "message_to_sales": "Looks clean. For once."}
