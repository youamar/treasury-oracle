from app.fuzzy import name_similarity, ref_overlap, soft_match_score, remember_alias


def test_name_similarity_identical():
    assert name_similarity("Acme Corp", "Acme Corp") > 0.95


def test_name_similarity_with_suffix():
    # Suffixes like "Sdn Bhd" are stripped; should still match well
    assert name_similarity("BrightTech Sdn Bhd", "BrightTech") > 0.8


def test_name_similarity_disjoint():
    assert name_similarity("Acme Corp", "Tokyo Robotics") < 0.5


def test_name_similarity_empty():
    assert name_similarity("", "x") == 0.0


def test_ref_overlap_exact():
    assert ref_overlap("INV-2026-001", "INWARD TT REF INV-2026-001") == 1.0


def test_ref_overlap_partial():
    score = ref_overlap("INV-2026-001", "INV2026001")
    assert 0.5 <= score <= 1.0


def test_soft_match_score_strong():
    proof = {"payer": "Acme Corp", "reference": "INV-001"}
    txn = {"description": "INWARD TT ACME CORP INV-001", "reference": ""}
    s = soft_match_score(proof, txn)
    assert s["score"] >= 0.8
    assert s["signals"]


def test_soft_match_score_weak():
    proof = {"payer": "Acme Corp", "reference": "INV-001"}
    txn = {"description": "Totally unrelated transfer", "reference": ""}
    s = soft_match_score(proof, txn)
    assert s["score"] < 0.4


def test_remember_alias_boost():
    proof = {"payer": "John Smith", "reference": ""}
    txn = {"description": "ACME CORP PAYMENT", "reference": ""}
    before = soft_match_score(proof, txn)["score"]
    remember_alias("John Smith", "ACME CORP")
    after = soft_match_score(proof, txn)["score"]
    assert after > before
