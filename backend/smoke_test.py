"""Real smoke test against live Chutes API.

Runs the full pipeline on the generated sample data:
  1. OCR all 8 payment proofs via Chutes vision LLM
  2. Parse the bank statement
  3. Reconcile
  4. Generate PDF report + audit pack
  5. Verify counts match expected

Usage:  python smoke_test.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.ocr import extract_payment_proof
from app.parser import parse_bank_statement
from app.matcher import reconcile
from app.report import build_report_pdf
from app.audit_pack import build_audit_pack

SAMPLES = Path(__file__).resolve().parent / "data" / "samples"


def section(title): print(f"\n{'='*60}\n{title}\n{'='*60}")


def main():
    section("1. OCR — vision LLM extraction of 8 payment proofs")
    proofs = []
    for png in sorted(SAMPLES.glob("proof_*.png")):
        print(f"  → {png.name}", end=" ")
        try:
            r = extract_payment_proof(png.read_bytes(), png.name)
            if "error" in r:
                print(f"❌ {r.get('error', 'unknown')}")
            else:
                print(f"✓ {r.get('amount')} {r.get('currency')} on {r.get('date')} "
                      f"from {(r.get('payer') or '')[:25]}")
            proofs.append(r)
        except Exception as e:
            print(f"💥 {e}")
            proofs.append({"source_file": png.name, "error": str(e)})

    section("2. Parse bank statement")
    stmt = SAMPLES / "bank_statement.csv"
    txns = parse_bank_statement(stmt.read_bytes(), stmt.name)
    print(f"  Parsed {len(txns)} transactions")
    for t in txns:
        print(f"    {t['id']}: {t['amount']} {t['currency']} on {t['date']}")

    section("3. Reconcile")
    result = reconcile(proofs, txns, bank="Maybank")
    s = result["summary"]
    print(f"  Strict matches : {s['matched']}")
    print(f"  Soft matches   : {s['soft_matches']}")
    print(f"  Unmatched      : {s['unmatched_proofs']}")
    print(f"  Orphan txns    : {s['unmatched_txns']}")
    print("\n  Trace:")
    for line in result["trace"]:
        print("   " + line)

    section("4. Generate report PDF")
    pdf = build_report_pdf(result, "Maybank")
    out = SAMPLES / "report.pdf"; out.write_bytes(pdf)
    print(f"  Report  → {out}  ({len(pdf)} bytes)")

    if result["matches"]:
        pack = build_audit_pack(result["matches"][0], "Maybank")
        out2 = SAMPLES / "audit_pack_sample.pdf"; out2.write_bytes(pack)
        print(f"  Audit Pack → {out2}  ({len(pack)} bytes)")

    section("5. Verdict")
    expected_matches = 6  # proofs 1-6
    if s["matched"] >= expected_matches - 1:  # allow ±1 for OCR variance
        print(f"  ✅ PASS — {s['matched']}/{expected_matches} expected matches")
    else:
        print(f"  ⚠ Below expected: {s['matched']}/{expected_matches}")
    print(f"  Soft match (proof_08): {'✅' if s['soft_matches'] >= 1 else '⚠'} got {s['soft_matches']}")
    print(f"  SWIFT trace fired   : "
          f"{'✅' if any(u.get('swift_route') for u in result['unmatched_proofs']) else '⚠'}")


if __name__ == "__main__":
    main()
