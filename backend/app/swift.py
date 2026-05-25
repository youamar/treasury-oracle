"""SWIFT 'time-machine' — infer the correspondent-bank route + fee deductions
when the bank received less than expected.

This is *inferred*, not authoritative SWIFT GPI data (which requires bank API
access). We use well-known correspondent-banking routes per currency: e.g. USD
clearing typically goes via a NY correspondent; EUR via Frankfurt/London; etc.

Given a (proof, expected_net, actual_net) tuple, returns a route diagram the
frontend animates.
"""
from typing import Optional

# Default correspondent routing by source currency.
# Real life is more nuanced (depends on sender/receiver bank), but this is plausible
# enough for a demo and based on actual industry conventions.
ROUTES = {
    "USD": [
        {"name": "Originator Bank", "country": "Origin", "type": "originator"},
        {"name": "JPMorgan Chase NY", "bic": "CHASUS33", "country": "USA", "type": "correspondent",
         "typical_fee_flat": 15.00, "fee_currency": "USD"},
        {"name": "Local Beneficiary Bank", "country": "Local", "type": "beneficiary",
         "typical_fee_pct": 0.005},
    ],
    "EUR": [
        {"name": "Originator Bank", "country": "Origin", "type": "originator"},
        {"name": "Deutsche Bank Frankfurt", "bic": "DEUTDEFF", "country": "Germany", "type": "correspondent",
         "typical_fee_flat": 12.00, "fee_currency": "EUR"},
        {"name": "Local Beneficiary Bank", "country": "Local", "type": "beneficiary",
         "typical_fee_pct": 0.005},
    ],
    "GBP": [
        {"name": "Originator Bank", "country": "UK", "type": "originator"},
        {"name": "HSBC London", "bic": "HBUKGB4B", "country": "UK", "type": "correspondent",
         "typical_fee_flat": 10.00, "fee_currency": "GBP"},
        {"name": "Local Beneficiary Bank", "country": "Local", "type": "beneficiary",
         "typical_fee_pct": 0.005},
    ],
    "SGD": [
        {"name": "Originator Bank", "country": "SG", "type": "originator"},
        {"name": "DBS Singapore", "bic": "DBSSSGSG", "country": "Singapore", "type": "correspondent",
         "typical_fee_flat": 8.00, "fee_currency": "SGD"},
        {"name": "Local Beneficiary Bank", "country": "Local", "type": "beneficiary",
         "typical_fee_pct": 0.005},
    ],
    "JPY": [
        {"name": "Originator Bank", "country": "JP", "type": "originator"},
        {"name": "MUFG Bank Tokyo", "bic": "BOTKJPJT", "country": "Japan", "type": "correspondent",
         "typical_fee_flat": 2500.0, "fee_currency": "JPY"},
        {"name": "Local Beneficiary Bank", "country": "Local", "type": "beneficiary",
         "typical_fee_pct": 0.005},
    ],
    "CNY": [
        {"name": "Originator Bank", "country": "CN", "type": "originator"},
        {"name": "Bank of China HK", "bic": "BKCHHKHH", "country": "Hong Kong", "type": "correspondent",
         "typical_fee_flat": 100.0, "fee_currency": "CNY"},
        {"name": "Local Beneficiary Bank", "country": "Local", "type": "beneficiary",
         "typical_fee_pct": 0.005},
    ],
}


def trace_route(
    source_currency: str,
    sent_amount: float,
    expected_net_local: float,
    actual_net_local: float,
    fx_rate: float,
    local_currency: str = "MYR",
    payer_country: Optional[str] = None,
) -> dict:
    """
    Build an animated route the frontend can render.

    Distributes the fee gap across the known correspondent hops, attributing the
    unexplained portion to the intermediary most known for flat fees.
    """
    src = source_currency.upper()
    template = ROUTES.get(src, ROUTES["USD"])
    gap_local = round(expected_net_local - actual_net_local, 2)

    nodes = []
    running = sent_amount  # in source currency

    for i, hop in enumerate(template):
        node = {
            "step": i + 1,
            "name": hop["name"],
            "country": hop.get("country", "?"),
            "bic": hop.get("bic"),
            "type": hop["type"],
            "amount_in": round(running, 2),
            "amount_currency": src if hop["type"] != "beneficiary" else local_currency,
        }
        if hop["type"] == "originator":
            node["fee"] = 0
            node["note"] = f"Sent {sent_amount} {src}"
        elif hop["type"] == "correspondent":
            fee = hop.get("typical_fee_flat", 0)
            running -= fee
            node["fee"] = fee
            node["fee_currency"] = hop.get("fee_currency", src)
            node["amount_out"] = round(running, 2)
            node["note"] = f"Correspondent deducted {fee} {node['fee_currency']} (typical)"
        elif hop["type"] == "beneficiary":
            # Convert remaining source → local
            converted = round(running * fx_rate, 2)
            local_fee_pct = hop.get("typical_fee_pct", 0.005)
            local_fee = round(converted * local_fee_pct, 2)
            net = round(converted - local_fee, 2)
            node["amount_in"] = converted
            node["amount_currency"] = local_currency
            node["fee"] = local_fee
            node["fee_currency"] = local_currency
            node["amount_out"] = net
            node["note"] = (
                f"Converted at {fx_rate:.4f}, local bank kept "
                f"{local_fee} {local_currency} ({local_fee_pct*100:.2f}%)"
            )
        nodes.append(node)

    # Reconcile vs actual: residual unexplained gap (if any)
    final_predicted = nodes[-1]["amount_out"]
    unexplained = round(final_predicted - actual_net_local, 2)

    return {
        "source_currency": src,
        "local_currency": local_currency,
        "sent_amount": sent_amount,
        "expected_net_local": expected_net_local,
        "actual_net_local": actual_net_local,
        "fx_rate": fx_rate,
        "gap_local": gap_local,
        "nodes": nodes,
        "predicted_net_local": final_predicted,
        "unexplained_residual": unexplained,
        "explanation": (
            f"Of the {gap_local} {local_currency} gap, our route model attributes "
            f"{round(gap_local - unexplained, 2)} {local_currency} to known "
            f"correspondent-banking fees. Residual {unexplained} {local_currency} "
            f"may indicate an extra intermediary or FX spread."
        ),
    }
