"""Bank statement parser — CSV/XLSX into normalized transactions.

Returns a list[dict] for back-compat. Callers wanting visibility into dropped
rows / detected columns should use `parse_bank_statement_detailed`, which
returns:

    {
      "transactions": [...],
      "skipped":      [{"row_index": int, "reason": str, "values": {...}}],
      "columns_detected": {"date": "...", "amount": "...", ...},
      "warnings":     [str, ...],   # high-level (e.g. dayfirst ambiguity)
      "row_count":    int,
    }

Silent row drops were a real reconciliation hazard — a 100-row statement
becoming 73 transactions with no audit trail made it impossible to explain
'where did the other 27 go?' to an auditor.
"""
import io
import pandas as pd
from datetime import datetime


COLUMN_ALIASES = {
    "date": ["date", "transaction date", "trans date", "posting date", "value date"],
    "amount": ["amount", "credit", "credit amount", "amount (credit)", "deposit"],
    "currency": ["currency", "ccy"],
    "description": ["description", "narrative", "details", "remarks", "memo", "particulars"],
    "reference": ["reference", "ref", "ref no", "transaction id", "txn id"],
}


def _find_column(df: pd.DataFrame, aliases: list[str]) -> str | None:
    cols_lower = {c.lower().strip(): c for c in df.columns}
    for alias in aliases:
        if alias in cols_lower:
            return cols_lower[alias]
    return None


def _parse_date(val, dayfirst: bool) -> str | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val).date().isoformat()
        except ValueError:
            pass
    try:
        return pd.to_datetime(val, dayfirst=dayfirst).date().isoformat()
    except Exception:
        return None


def parse_bank_statement_detailed(file_bytes: bytes, filename: str,
                                  dayfirst: bool = True) -> dict:
    """Full parse with skipped-row provenance. `dayfirst=True` defaults to the
    Malaysian/EU convention; pass False explicitly for US-format statements."""
    name = filename.lower()
    if name.endswith(".xlsx") or name.endswith(".xls"):
        df = pd.read_excel(io.BytesIO(file_bytes))
    else:
        df = pd.read_csv(io.BytesIO(file_bytes))

    date_col = _find_column(df, COLUMN_ALIASES["date"])
    amount_col = _find_column(df, COLUMN_ALIASES["amount"])
    ccy_col = _find_column(df, COLUMN_ALIASES["currency"])
    desc_col = _find_column(df, COLUMN_ALIASES["description"])
    ref_col = _find_column(df, COLUMN_ALIASES["reference"])

    if not date_col or not amount_col:
        raise ValueError(
            f"Statement missing required columns. Found: {list(df.columns)}. "
            f"Need at least a date and amount column."
        )

    columns_detected = {
        "date": date_col, "amount": amount_col, "currency": ccy_col,
        "description": desc_col, "reference": ref_col,
    }
    warnings: list[str] = []
    if not ccy_col:
        warnings.append("No currency column detected — defaulting all rows to MYR.")

    txns: list[dict] = []
    skipped: list[dict] = []

    for idx, row in df.iterrows():
        raw_amount = row[amount_col]
        try:
            amount = float(raw_amount)
        except (ValueError, TypeError):
            skipped.append({"row_index": int(idx), "reason": "unparseable_amount",
                            "values": {"amount": str(raw_amount)}})
            continue
        if pd.isna(amount):
            skipped.append({"row_index": int(idx), "reason": "blank_amount",
                            "values": {}})
            continue
        if amount == 0:
            # Zero-amount rows are useless for reconciliation but worth recording.
            skipped.append({"row_index": int(idx), "reason": "zero_amount",
                            "values": {}})
            continue

        date_iso = _parse_date(row[date_col], dayfirst=dayfirst)
        if date_iso is None:
            skipped.append({"row_index": int(idx), "reason": "unparseable_date",
                            "values": {"date": str(row[date_col])}})
            continue

        # Direction tagging — preserve the row regardless of sign so reconcilers
        # can match refunds/reversals against the right invoice, and so the
        # audit pack can show the *complete* statement, not a filtered subset.
        direction = "in" if amount > 0 else "out"
        txns.append({
            "id": f"txn_{idx}",
            "date": date_iso,
            "amount": round(abs(amount), 2),
            "signed_amount": round(amount, 2),
            "direction": direction,
            "currency": (str(row[ccy_col]).upper() if ccy_col else "MYR"),
            "description": (str(row[desc_col]) if desc_col else ""),
            "reference": (str(row[ref_col]) if ref_col else ""),
        })

    return {
        "transactions": txns,
        "skipped": skipped,
        "columns_detected": columns_detected,
        "headers": [str(c) for c in df.columns],
        "warnings": warnings,
        "row_count": int(len(df)),
        "inbound_count": sum(1 for t in txns if t["direction"] == "in"),
        "outbound_count": sum(1 for t in txns if t["direction"] == "out"),
    }


def parse_bank_statement(file_bytes: bytes, filename: str,
                         dayfirst: bool = True,
                         include_outbound: bool = False) -> list[dict]:
    """Back-compat thin wrapper. Returns just the transactions list.

    By default filters to inbound (direction='in') because legacy callers
    treated the list as 'inbound payments to reconcile against'. Set
    `include_outbound=True` to get both directions tagged."""
    txns = parse_bank_statement_detailed(file_bytes, filename, dayfirst=dayfirst)["transactions"]
    if include_outbound:
        return txns
    return [t for t in txns if t.get("direction", "in") == "in"]
