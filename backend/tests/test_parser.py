import io
import pandas as pd
import pytest
from app.parser import parse_bank_statement


def _csv(rows, headers):
    df = pd.DataFrame(rows, columns=headers)
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


def test_parse_basic_csv():
    data = _csv(
        [["2026-05-20", 4700.00, "MYR", "INWARD TT ACME", "INV-001"]],
        ["Date", "Amount", "Currency", "Description", "Reference"],
    )
    txns = parse_bank_statement(data, "stmt.csv")
    assert len(txns) == 1
    assert txns[0]["amount"] == 4700.00
    assert txns[0]["currency"] == "MYR"
    assert txns[0]["reference"] == "INV-001"


def test_parse_skips_debits_and_zeros():
    data = _csv(
        [["2026-05-20", -100.0, "MYR", "DEBIT", "x"],
         ["2026-05-21", 0.0, "MYR", "ZERO", "y"],
         ["2026-05-22", 500.0, "MYR", "CREDIT", "z"]],
        ["Date", "Amount", "Currency", "Description", "Reference"],
    )
    txns = parse_bank_statement(data, "stmt.csv")
    assert len(txns) == 1
    assert txns[0]["amount"] == 500.0


def test_parse_column_aliases():
    data = _csv(
        [["20/05/2026", 100.0, "USD", "blah"]],
        ["Transaction Date", "Credit", "CCY", "Narrative"],
    )
    txns = parse_bank_statement(data, "stmt.csv")
    assert len(txns) == 1
    assert txns[0]["date"].startswith("2026-05")


def test_parse_missing_columns_raises():
    data = _csv([[1, 2]], ["foo", "bar"])
    with pytest.raises(ValueError):
        parse_bank_statement(data, "stmt.csv")


def test_parse_xlsx():
    df = pd.DataFrame(
        [["2026-05-20", 4700.0, "MYR", "test", "ref1"]],
        columns=["Date", "Amount", "Currency", "Description", "Reference"],
    )
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    txns = parse_bank_statement(buf.getvalue(), "stmt.xlsx")
    assert len(txns) == 1
