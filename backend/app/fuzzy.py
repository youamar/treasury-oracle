"""Soft / fuzzy match tier — the 'uncle's account' case.

When strict amount+currency matching fails, try:
  - name similarity (payer vs txn description) via rapidfuzz
  - reference / invoice number overlap
  - historical payer learning (in-memory for the hackathon)

Returns soft-match candidates with a confidence score; UI asks the user to confirm.
"""
from rapidfuzz import fuzz
import re
from . import db


def _normalize(s: str) -> str:
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # drop common entity suffixes
    for suf in [" sdn bhd", " pte ltd", " ltd", " gmbh", " kk", " co", " corp",
                " llc", " inc", " bhd", " holdings"]:
        s = s.replace(suf, "")
    return s.strip()


def name_similarity(a: str, b: str) -> float:
    a, b = _normalize(a), _normalize(b)
    if not a or not b:
        return 0.0
    return fuzz.token_set_ratio(a, b) / 100.0


def ref_overlap(ref_a: str, text_b: str) -> float:
    """1.0 if invoice ref appears in txn description, else partial token match."""
    if not ref_a or not text_b:
        return 0.0
    a = ref_a.upper().strip()
    if a in text_b.upper():
        return 1.0
    return fuzz.partial_ratio(a, text_b.upper()) / 100.0


def soft_match_score(proof: dict, txn: dict) -> dict:
    """
    Score a (proof, txn) pair where strict matching failed.
    Combines name similarity + ref overlap + amount-ratio sanity.
    """
    payer = proof.get("payer") or ""
    ref = proof.get("reference") or ""
    desc = txn.get("description") or ""
    txn_ref = txn.get("reference") or ""

    name_sim = name_similarity(payer, desc)
    ref_sim = max(ref_overlap(ref, desc), ref_overlap(ref, txn_ref))

    # Check historical aliases — persisted in SQLite
    alias_boost = 0.0
    norm_payer = _normalize(payer)
    observed = db.lookup_alias(norm_payer)
    if observed and observed in _normalize(desc):
        alias_boost = 0.2

    score = max(name_sim * 0.5 + ref_sim * 0.5, ref_sim) + alias_boost
    score = min(1.0, score)

    signals = []
    if ref_sim >= 0.9:
        signals.append(f"Invoice reference '{ref}' found in bank description")
    elif ref_sim >= 0.5:
        signals.append(f"Partial reference match ({ref_sim*100:.0f}%)")
    if name_sim >= 0.7:
        signals.append(f"Payer name highly similar ({name_sim*100:.0f}%)")
    elif name_sim >= 0.4:
        signals.append(f"Payer name partially matches ({name_sim*100:.0f}%)")
    if alias_boost > 0:
        signals.append("Historical alias on record")

    return {
        "score": round(score, 3),
        "name_similarity": round(name_sim, 3),
        "ref_similarity": round(ref_sim, 3),
        "signals": signals,
    }


def remember_alias(canonical_payer: str, observed_in_txn: str):
    """Called when user confirms a soft match — learn this mapping for next time."""
    db.remember_alias(_normalize(canonical_payer), _normalize(observed_in_txn))
