from app.report import build_report_pdf
from app.audit_pack import build_audit_pack
from app.matcher import reconcile


def _result(sample_proof, sample_txn):
    return reconcile([sample_proof], [sample_txn], bank="Maybank")


def test_report_pdf_is_valid(sample_proof, sample_txn):
    pdf = build_report_pdf(_result(sample_proof, sample_txn), "Maybank")
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 1000


def test_audit_pack_pdf_is_valid(sample_proof, sample_txn):
    result = _result(sample_proof, sample_txn)
    assert result["matches"], "expected at least one match for audit pack"
    pdf = build_audit_pack(result["matches"][0], "Maybank")
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 1000


def test_report_with_no_matches():
    pdf = build_report_pdf({
        "matches": [], "soft_matches": [], "unmatched_proofs": [],
        "unmatched_txns": [], "trace": [],
        "summary": {"total_proofs": 0, "total_txns": 0, "matched": 0,
                    "unmatched_proofs": 0, "unmatched_txns": 0},
    }, "Maybank")
    assert pdf.startswith(b"%PDF-")
