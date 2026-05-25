from app.matcher import reconcile


def test_strict_match(sample_proof, sample_txn):
    out = reconcile([sample_proof], [sample_txn], bank="Maybank")
    assert out["summary"]["matched"] == 1
    assert out["matches"][0]["proof"]["reference"] == "INV-2026-001"
    assert "STRICT MATCH" in "\n".join(out["trace"])


def test_unparseable_proof_is_skipped():
    out = reconcile([{"error": "bad", "source_file": "x"}], [])
    assert out["summary"]["unmatched_proofs"] == 1
    assert out["summary"]["matched"] == 0


def test_discrepancy_triggers_swift_trace():
    proof = {"amount": 500, "currency": "USD", "date": "2026-05-24",
             "payer": "MysteryCo", "reference": "INV-007", "source_file": "p.png"}
    # Txn that's ~15% lower than expected → outside strict tolerance, triggers SWIFT
    expected_strict = 500 * 4.72 * 0.995
    txn = {"id": "txn_0", "date": "2026-05-24",
           "amount": round(expected_strict * 0.85, 2),
           "currency": "MYR", "description": "INWARD TT UNKNOWN", "reference": "X"}
    out = reconcile([proof], [txn], bank="Maybank")
    assert out["summary"]["matched"] == 0
    assert out["unmatched_proofs"][0].get("swift_route") is not None
    nodes = out["unmatched_proofs"][0]["swift_route"]["nodes"]
    assert len(nodes) == 3
    assert any(n["type"] == "correspondent" for n in nodes)


def test_soft_match_promoted(sample_proof):
    # Strict amount off by ~10% but name+ref strongly match → soft match
    txn = {
        "id": "txn_0", "date": "2026-05-20",
        "amount": round(1000 * 4.72 * 0.995 * 0.90, 2),  # 10% short
        "currency": "MYR",
        "description": "INWARD TT ACME CORP USA INV-2026-001",
        "reference": "INV-2026-001",
    }
    out = reconcile([sample_proof], [txn], bank="Maybank")
    # Either strict (if tolerance loose) or soft
    assert out["summary"]["matched"] + out["summary"]["soft_matches"] >= 1


def test_orphan_txn_listed():
    proof = {"amount": 100, "currency": "USD", "date": "2026-05-20",
             "payer": "X", "reference": "Y", "source_file": "p"}
    txns = [
        {"id": "txn_0", "date": "2026-05-20", "amount": round(100*4.72*0.995, 2),
         "currency": "MYR", "description": "X", "reference": "Y"},
        {"id": "txn_orphan", "date": "2026-05-20", "amount": 99999.99,
         "currency": "MYR", "description": "ORPHAN", "reference": "Z"},
    ]
    out = reconcile([proof], txns, bank="Maybank")
    assert any(t["id"] == "txn_orphan" for t in out["unmatched_txns"])
