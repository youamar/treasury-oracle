"""Reconciliation matcher agent.

Two-tier matching:
  Tier 1: STRICT — amount within 2% after FX + bank fees, within 5-day window
  Tier 2: SOFT  — fuzzy name / invoice-ref / historical-alias match (requires UI confirm)

When strict matching produces a non-trivial gap, the SWIFT route tracer is
invoked so the frontend can animate where the missing money went.
"""
from datetime import datetime
from .tools import get_fx_rate, apply_bank_fee
from .config import MATCH_TOLERANCE
from .fuzzy import soft_match_score
from .swift import trace_route


SOFT_MATCH_THRESHOLD = 0.55  # below this, don't even propose
GAP_TRACE_THRESHOLD = 0.05   # >5% gap → invoke SWIFT trace


def _date_diff_days(d1: str, d2: str) -> int:
    try:
        return abs((datetime.fromisoformat(d1) - datetime.fromisoformat(d2)).days)
    except Exception:
        return 999


def _score_pair(proof, txn, bank):
    """Return strict-match candidate dict."""
    proof_amount = float(proof["amount"])
    proof_ccy = str(proof["currency"]).upper()
    proof_date = proof.get("date") or datetime.today().date().isoformat()
    target_ccy = txn["currency"].upper()

    rate = get_fx_rate(proof_ccy, target_ccy, proof_date)
    gross = round(proof_amount * rate, 2)
    fee_info = apply_bank_fee(gross, bank)
    expected_net = fee_info["net_amount"]
    actual = float(txn["amount"])
    denom = max(expected_net, 1e-6)
    diff_pct = abs(actual - expected_net) / denom
    days_off = _date_diff_days(proof_date, txn["date"])

    return {
        "txn": txn,
        "rate": rate,
        "expected_gross": gross,
        "expected_net": expected_net,
        "actual": actual,
        "diff_pct": diff_pct,
        "days_off": days_off,
        "fee_info": fee_info,
        "score": (1.0 - diff_pct) - 0.02 * days_off,
    }


def reconcile(proofs: list[dict], txns: list[dict], bank: str = "default") -> dict:
    matches = []
    soft_matches = []        # need user confirmation
    unmatched_proofs = []
    used_txn_ids = set()
    trace = []

    for proof in proofs:
        if "error" in proof or not proof.get("amount") or not proof.get("currency"):
            unmatched_proofs.append({**proof, "reason": "Proof could not be parsed"})
            trace.append(f"SKIP {proof.get('source_file','?')} — unreadable")
            continue

        proof_ccy = str(proof["currency"]).upper()
        proof_date = proof.get("date") or datetime.today().date().isoformat()
        trace.append(
            f"PROOF {proof.get('source_file','?')}: {proof['amount']} {proof_ccy} on {proof_date}"
        )

        # --- Tier 1: strict candidates within date window ---
        candidates = []
        for txn in txns:
            if txn["id"] in used_txn_ids:
                continue
            if _date_diff_days(proof_date, txn["date"]) > 5:
                continue
            candidates.append(_score_pair(proof, txn, bank))

        best = max(candidates, key=lambda c: c["score"], default=None)

        if best and best["diff_pct"] <= MATCH_TOLERANCE:
            used_txn_ids.add(best["txn"]["id"])
            reasoning = (
                f"Converted {proof['amount']} {proof_ccy} → {best['expected_gross']} "
                f"{best['txn']['currency']} at FX rate {best['rate']:.4f} on {proof_date}. "
                f"After {best['fee_info']['fee_pct']*100:.2f}% bank fee "
                f"({best['fee_info']['fee_amount']}), expected net {best['expected_net']}. "
                f"Bank received {best['actual']} — diff {best['diff_pct']*100:.2f}% "
                f"(within {MATCH_TOLERANCE*100:.0f}% tolerance), {best['days_off']} day(s) apart."
            )
            matches.append({
                "proof": proof, "txn": best["txn"],
                "conversion": {
                    "fx_rate": best["rate"],
                    "expected_gross": best["expected_gross"],
                    "expected_net": best["expected_net"],
                    "actual_received": best["actual"],
                    "fee_pct": best["fee_info"]["fee_pct"],
                    "fee_amount": best["fee_info"]["fee_amount"],
                },
                "confidence": round(max(0.0, min(1.0, 1 - best["diff_pct"] * 5)), 2),
                "reasoning": reasoning,
                "status": "matched",
            })
            trace.append(f"  [OK] STRICT MATCH → {best['txn']['id']} (diff {best['diff_pct']*100:.2f}%)")
            continue

        # --- Tier 2: SOFT match (the 'uncle's account' case) ---
        soft_candidates = []
        for txn in txns:
            if txn["id"] in used_txn_ids:
                continue
            scored = _score_pair(proof, txn, bank)
            soft = soft_match_score(proof, txn)
            # Combine: if amount close-ish (within 15%) AND fuzzy signals strong, propose
            if scored["diff_pct"] < 0.15 and soft["score"] >= SOFT_MATCH_THRESHOLD:
                soft_candidates.append({
                    "txn": txn,
                    "amount_diff_pct": scored["diff_pct"],
                    "soft": soft,
                    "conversion": {
                        "fx_rate": scored["rate"],
                        "expected_net": scored["expected_net"],
                        "actual_received": scored["actual"],
                    },
                    "score": soft["score"] - scored["diff_pct"],
                })

        soft_candidates.sort(key=lambda c: c["score"], reverse=True)
        if soft_candidates:
            top = soft_candidates[0]
            confidence = round(min(0.99, top["soft"]["score"] * (1 - top["amount_diff_pct"])), 2)
            soft_matches.append({
                "proof": proof,
                "txn": top["txn"],
                "conversion": top["conversion"],
                "confidence": confidence,
                "signals": top["soft"]["signals"],
                "reasoning": (
                    f"Amount differs by {top['amount_diff_pct']*100:.1f}% but "
                    + "; ".join(top["soft"]["signals"])
                    + ". Awaiting confirmation."
                ),
                "status": "soft_match_pending",
            })
            trace.append(f"  [?] SOFT MATCH proposed → {top['txn']['id']} (conf {confidence*100:.0f}%)")
            continue

        # --- No match: emit discrepancy + SWIFT trace if there's a clear gap ---
        if best:
            swift_route = None
            # Only trace when a clear gap exists (>5%) and the closest txn is plausible
            if best["diff_pct"] > GAP_TRACE_THRESHOLD and best["actual"] < best["expected_net"]:
                swift_route = trace_route(
                    source_currency=proof_ccy,
                    sent_amount=float(proof["amount"]),
                    expected_net_local=best["expected_net"],
                    actual_net_local=best["actual"],
                    fx_rate=best["rate"],
                    local_currency=best["txn"]["currency"],
                )

            unmatched_proofs.append({
                **proof,
                "reason": (
                    f"Closest bank txn was {best['txn']['id']} ({best['actual']} "
                    f"{best['txn']['currency']}) but differed by {best['diff_pct']*100:.2f}% "
                    f"from expected {best['expected_net']} after FX + fees."
                ),
                "closest_txn": best["txn"],
                "expected_net": best["expected_net"],
                "actual": best["actual"],
                "fx_rate": best["rate"],
                "swift_route": swift_route,
            })
            trace.append(f"  [X] NO MATCH — gap {best['diff_pct']*100:.2f}%"
                         + (" · SWIFT route traced" if swift_route else ""))
        else:
            unmatched_proofs.append({**proof, "reason": "No bank transactions within date window"})
            trace.append("  [X] NO MATCH — no candidates in date window")

    unmatched_txns = [t for t in txns if t["id"] not in used_txn_ids
                      and not any(s["txn"]["id"] == t["id"] for s in soft_matches)]

    return {
        "matches": matches,
        "soft_matches": soft_matches,
        "unmatched_proofs": unmatched_proofs,
        "unmatched_txns": unmatched_txns,
        "trace": trace,
        "summary": {
            "total_proofs": len(proofs),
            "total_txns": len(txns),
            "matched": len(matches),
            "soft_matches": len(soft_matches),
            "unmatched_proofs": len(unmatched_proofs),
            "unmatched_txns": len(unmatched_txns),
        },
    }
