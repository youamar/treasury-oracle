"""Smoke test: run the AGENT (real Chutes tool-calling) on sample data."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.ocr import extract_payment_proof
from app.parser import parse_bank_statement
from app.agent import reconcile_agent
from app import db

SAMPLES = Path(__file__).resolve().parent / "data" / "samples"


def section(t): print(f"\n{'='*60}\n{t}\n{'='*60}")


def main():
    db.init_db()

    section("1. OCR")
    proofs = []
    for png in sorted(SAMPLES.glob("proof_*.png")):
        r = extract_payment_proof(png.read_bytes(), png.name)
        ok = "amount" in r and r.get("amount")
        print(f"  {'✓' if ok else '✗'} {png.name}: "
              f"{r.get('amount')} {r.get('currency')} {r.get('date')}")
        proofs.append(r)

    section("2. Parse statement")
    stmt = SAMPLES / "bank_statement.csv"
    txns = parse_bank_statement(stmt.read_bytes(), stmt.name)
    print(f"  {len(txns)} txns parsed")

    section("3. AGENT reconciliation (real Chutes tool calls)")
    result = reconcile_agent(proofs, txns, bank="Maybank")
    s = result["summary"]
    print(f"  Strict matches : {s['matched']}")
    print(f"  Soft matches   : {s['soft_matches']}")
    print(f"  Unmatched      : {s['unmatched_proofs']}")
    print(f"  Orphan txns    : {s['unmatched_txns']}")

    section("4. Agent trace (first 25 events)")
    trace = result["agent_trace"][:25]
    for t in trace:
        print(f"  [{t['step']}/{t['type']}] {str(t['payload'])[:90]}")

    section("5. Sample tool-call sequences")
    for m in result["matches"][:2]:
        print(f"\n  Match → {m['proof'].get('source_file')}")
        for tc in m.get("agent_tool_calls", []):
            print(f"    → {tc['name']}({tc['arguments']}) = "
                  f"{str(tc.get('result'))[:80]}")

    section("6. Verdict")
    if s["matched"] >= 5:
        print(f"  ✅ Agent matched {s['matched']}/8 proofs via real tool calls")
    else:
        print(f"  ⚠ Only {s['matched']}/8 matched — check trace above")
    print(f"  Session persisted to SQLite: recon_id={result['recon_id']}")

    # Verify persistence by reloading
    reloaded = db.load_session(result["recon_id"])
    print(f"  Reload from DB: {reloaded['summary']['matched']} matches recovered")


if __name__ == "__main__":
    main()
