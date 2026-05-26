"""Plain-English summary of a reconciliation session.

Run after a session completes. The LLM reads the summary + a digest of
trace events and produces a 2-3 paragraph 'story' that a non-technical
operator (or auditor) can read in 30 seconds:

  - what happened (X proofs in, Y matched, Z flagged)
  - what was notable (verifier downgrades, FX source warnings, partial payments)
  - what to do next (which discrepancies need attention, which soft-matches
    need confirmation)

Cached in the session row so re-displaying the same session doesn't spend
tokens twice.
"""
from __future__ import annotations

import json
from typing import Any

from .chutes_client import chat
from .config import MODEL_PROFILES
from . import reliability, db


NARRATIVE_PROMPT = """You are a treasury operations assistant writing a brief
hand-off note for a CFO who has 30 seconds to read.

Below is the JSON summary of a cross-border reconciliation run. Produce a
2-3 paragraph plain-English narrative that covers:

  1. WHAT HAPPENED — total proofs, matches, soft matches, discrepancies.
  2. WHAT'S NOTABLE — verifier downgrades, untrusted FX sources, partial
     payments, suspicious patterns. Skip if nothing notable.
  3. WHAT TO DO NEXT — specific actionable items (e.g. "confirm soft match
     for Acme INV-123", "follow up with MysteryCo on the shortfall").

Be concrete: cite invoice references, amounts, and payer names. Skip
generic statements ("the system performed well"). Don't list every match —
mention only the noteworthy ones.

Return STRICT JSON:
{
  "headline": "<one-sentence summary, max 12 words>",
  "paragraphs": ["<para 1>", "<para 2>", "<para 3 — optional>"],
  "action_items": ["<short specific action>", ...]
}"""


def _digest_session(session: dict) -> str:
    """Compact JSON the LLM can read — strips verbose proof images / trace blobs."""
    matches_digest = []
    for m in session.get("matches", []) or []:
        prov = (m.get("conversion") or {}).get("provenance") or {}
        verifier = prov.get("verifier") or {}
        matches_digest.append({
            "ref": m["proof"].get("reference"),
            "payer": m["proof"].get("payer"),
            "amount_proof": f"{m['proof'].get('amount')} {m['proof'].get('currency')}",
            "amount_received": f"{m['txn'].get('amount')} {m['txn'].get('currency')}",
            "confidence": m.get("confidence"),
            "verifier_verdict": verifier.get("verdict"),
            "verifier_ensemble": verifier.get("ensemble"),
            "fx_source": (prov.get("fx_rate") or {}).get("source"),
            "all_inputs_trusted": prov.get("all_inputs_trusted"),
        })

    soft_digest = []
    for s in session.get("soft_matches", []) or []:
        soft_digest.append({
            "ref": s["proof"].get("reference"),
            "payer": s["proof"].get("payer"),
            "amount": f"{s['proof'].get('amount')} {s['proof'].get('currency')}",
            "txn_amount": f"{s['txn'].get('amount')} {s['txn'].get('currency')}",
            "signals": s.get("signals"),
            "reasoning": s.get("reasoning"),
        })

    disc_digest = []
    for u in session.get("unmatched_proofs", []) or []:
        disc_digest.append({
            "ref": u.get("reference"),
            "payer": u.get("payer"),
            "amount": f"{u.get('amount')} {u.get('currency')}",
            "reason": u.get("reason"),
            "needs_review": bool(u.get("needs_review")),
        })

    return json.dumps({
        "bank": session.get("bank"),
        "summary": session.get("summary"),
        "matches": matches_digest,
        "soft_matches": soft_digest,
        "discrepancies": disc_digest,
        "unmatched_txns": len(session.get("unmatched_txns") or []),
    }, default=str, ensure_ascii=False)


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    if not t.startswith("```"):
        return t
    parts = t.split("```")
    if len(parts) < 2:
        return t
    inner = parts[1]
    if inner.startswith("json"):
        inner = inner[4:]
    return inner.strip()


def generate_narrative(session: dict,
                       model_profile: str = "default",
                       timeout: float = 25.0) -> dict:
    """Generate the narrative. Returns dict ready to render. Falls back to a
    deterministic template if the LLM is unavailable so the UI never blanks."""
    digest = _digest_session(session)
    model = MODEL_PROFILES.get(model_profile, MODEL_PROFILES["default"])

    try:
        resp = chat(
            messages=[
                {"role": "system", "content": NARRATIVE_PROMPT},
                {"role": "user", "content": f"SESSION DIGEST:\n{digest}"},
            ],
            model=model,
            temperature=0.4,
            max_tokens=600,
            response_format={"type": "json_object"},
            timeout=timeout,
        )
    except Exception as e:
        reliability.record_error("narrative.generate", e,
                                 context={"recon_id": session.get("recon_id")})
        return _fallback_narrative(session, reason=f"{type(e).__name__}: {e}")

    raw = _strip_fences((resp.choices[0].message.content or "").strip())
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _fallback_narrative(session, reason="LLM returned non-JSON")

    if not isinstance(data.get("paragraphs"), list):
        return _fallback_narrative(session, reason="LLM JSON missing paragraphs")

    data.setdefault("action_items", [])
    data.setdefault("headline", "Reconciliation complete")
    data["generated"] = "llm"
    return data


def _fallback_narrative(session: dict, reason: str = "") -> dict:
    """Deterministic template when the LLM is down. Reads like a human summary
    even without any model access — much better than nothing for the UI."""
    s = session.get("summary") or {}
    bank = session.get("bank", "the bank")
    matched = s.get("matched", 0)
    soft = s.get("soft_matches", 0)
    disc = s.get("unmatched_proofs", 0)
    total = s.get("total_proofs", 0)

    paragraphs = [
        f"Processed {total} payment proof{'s' if total != 1 else ''} against {bank}'s "
        f"statement: {matched} strict match{'es' if matched != 1 else ''}, "
        f"{soft} soft match{'es' if soft != 1 else ''} awaiting your confirmation, "
        f"and {disc} discrepanc{'ies' if disc != 1 else 'y'} flagged for review.",
    ]
    if soft > 0:
        soft_list = session.get("soft_matches") or []
        names = [s["proof"].get("payer") or s["proof"].get("reference", "?")
                 for s in soft_list[:3]]
        paragraphs.append(
            f"Soft matches needing review: {', '.join(names)}. "
            f"Confirm each from the Reconcile view so the agent learns the alias."
        )
    if disc > 0:
        d_list = session.get("unmatched_proofs") or []
        refs = [u.get("reference") or u.get("source_file", "?") for u in d_list[:3]]
        paragraphs.append(
            f"Discrepancies to address: {', '.join(refs)}. "
            f"Open each from the Discrepancies list to draft a dunning email or trace SWIFT routing."
        )

    actions = []
    if soft > 0:
        actions.append(f"Confirm {soft} soft match{'es' if soft != 1 else ''}")
    if disc > 0:
        actions.append(f"Draft dunning emails for {disc} flagged item{'s' if disc != 1 else ''}")
    if matched == total and total > 0:
        actions.append("Everything matched — download the PDF reconciliation report")

    return {
        "headline": f"{matched} of {total} proofs matched, {disc} need attention",
        "paragraphs": paragraphs,
        "action_items": actions,
        "generated": "fallback",
        "fallback_reason": reason,
    }


# ---------- caching ----------

def get_or_create(recon_id: str, force: bool = False) -> dict | None:
    """Look up a cached narrative for the session; generate if missing.
    Returns None if the session itself doesn't exist."""
    session = db.load_session(recon_id)
    if not session:
        return None

    if not force:
        cached = db.recall_facts(subject=f"narrative:{recon_id}",
                                 predicate="json", limit=1)
        if cached:
            try:
                return json.loads(cached[0]["value"])
            except Exception:
                pass

    narrative = generate_narrative(session)
    try:
        db.remember_fact(subject=f"narrative:{recon_id}",
                         predicate="json",
                         value=json.dumps(narrative, default=str),
                         source=f"narrative:{recon_id}")
    except Exception:
        pass
    return narrative
