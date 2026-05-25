"""Bank statement parser — CSV/XLSX into normalized transactions."""
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


def parse_bank_statement(file_bytes: bytes, filename: str) -> list[dict]:
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

    txns = []
    for idx, row in df.iterrows():
        try:
            amount = float(row[amount_col])
        except (ValueError, TypeError):
            continue
        # Skip debits / zero rows (we reconcile inbound payments)
        if amount <= 0:
            continue

        date_val = row[date_col]
        if isinstance(date_val, str):
            try:
                date_iso = datetime.fromisoformat(date_val).date().isoformat()
            except ValueError:
                try:
                    date_iso = pd.to_datetime(date_val).date().isoformat()
                except Exception:
                    continue
        else:
            try:
                date_iso = pd.to_datetime(date_val).date().isoformat()
            except Exception:
                continue

        txns.append({
            "id": f"txn_{idx}",
            "date": date_iso,
            "amount": round(amount, 2),
            "currency": (str(row[ccy_col]).upper() if ccy_col else "MYR"),
            "description": (str(row[desc_col]) if desc_col else ""),
            "reference": (str(row[ref_col]) if ref_col else ""),
        })

    return txns
