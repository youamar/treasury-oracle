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


def test_column_drift_first_upload_is_not_drift():
    from app import db
    drift = db.compute_column_drift(None, ["Date","Amount"],
                                    {"date":"Date","amount":"Amount"})
    assert drift["drift"] is False
    assert drift["is_first_seen"] is True


def test_column_drift_detects_rename():
    from app import db
    previous = {
        "headers": ["Date","Amount","Currency"],
        "columns_detected": {"date":"Date","amount":"Amount","currency":"Currency",
                             "description":None,"reference":None},
        "updated_at": "2026-05-01T00:00:00",
    }
    drift = db.compute_column_drift(
        previous,
        ["Posting Date","Credit Amount","CCY"],
        {"date":"Posting Date","amount":"Credit Amount","currency":"CCY",
         "description":None,"reference":None},
    )
    assert drift["drift"] is True
    assert drift["severity"] == "fields_moved"  # date+amount both moved → critical
    fields = {c["field"] for c in drift["changes"]}
    assert {"date","amount","currency"} <= fields


def test_column_drift_severity_is_renamed_only_for_nice_to_have():
    from app import db
    previous = {
        "headers": ["Date","Amount","Description"],
        "columns_detected": {"date":"Date","amount":"Amount","description":"Description",
                             "currency":None,"reference":None},
        "updated_at": "2026-05-01T00:00:00",
    }
    drift = db.compute_column_drift(
        previous,
        ["Date","Amount","Narrative"],
        {"date":"Date","amount":"Amount","description":"Narrative",
         "currency":None,"reference":None},
    )
    assert drift["drift"] is True
    # description moved but date+amount unchanged → non-critical
    assert drift["severity"] == "headers_renamed"


def test_parse_statement_endpoint_persists_and_detects_drift():
    """End-to-end: same bank, second upload with renamed amount column → drift."""
    import io
    import pandas as pd
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)

    def _csv_bytes(rows, headers):
        df = pd.DataFrame(rows, columns=headers)
        buf = io.BytesIO(); df.to_csv(buf, index=False); return buf.getvalue()

    # First upload — original headers
    b1 = _csv_bytes([["2026-05-20", 100.0, "MYR"]], ["Date","Amount","Currency"])
    r1 = client.post("/api/parse-statement?bank=DriftTest",
                     files={"file": ("a.csv", b1, "text/csv")})
    assert r1.status_code == 200
    j1 = r1.json()
    assert j1["column_drift"]["drift"] is False
    assert j1["column_drift"]["is_first_seen"] is True

    # Second upload — same bank, "Amount" renamed to "Credit"
    b2 = _csv_bytes([["2026-05-21", 200.0, "MYR"]], ["Date","Credit","Currency"])
    r2 = client.post("/api/parse-statement?bank=DriftTest",
                     files={"file": ("b.csv", b2, "text/csv")})
    j2 = r2.json()
    assert j2["column_drift"]["drift"] is True
    assert j2["column_drift"]["severity"] == "fields_moved"
    moved = [c for c in j2["column_drift"]["changes"] if c["field"] == "amount"]
    assert moved and moved[0]["previous_column"] == "Amount"
    assert moved[0]["current_column"] == "Credit"
