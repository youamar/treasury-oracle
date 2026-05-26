"""F11+F12 — Audit pack tamper-evidence."""
import base64
import io
import json
import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import attestation, db
from app.audit_pack import build_audit_pack
from app.main import app


client = TestClient(app)


def _stash_proof_bytes(content: bytes = b"\x89PNG\r\nfakeproof") -> str:
    """Store some bytes via raw_uploads and return the SHA."""
    sha = hashlib.sha256(content).hexdigest()
    rec = db.find_upload_by_sha(sha)
    if rec is None:
        from app import uploads
        rec = uploads.store_bytes("proof_t.png", content, purpose="proof")
    return sha


def _basic_match(sha: str | None) -> dict:
    return {
        "proof": {"amount": 1000.0, "currency": "USD", "date": "2026-05-20",
                  "payer": "Acme Corp", "payee": "BrightTech",
                  "reference": "INV-T-001", "source_file": "p.png",
                  "source_sha256": sha},
        "txn": {"id": "txn_t_1", "date": "2026-05-20", "amount": 4696.40,
                "currency": "MYR", "description": "INWARD TT ACME",
                "reference": "INV-T-001"},
        "conversion": {
            "fx_rate": 4.72, "expected_gross": 4720.0, "expected_net": 4696.40,
            "actual_received": 4696.40, "fee_pct": 0.005, "fee_amount": 23.6,
            "provenance": {
                "proof_amount": {"value": 1000.0, "currency": "USD",
                                 "source": "ocr:p.png",
                                 "source_sha256": sha, "trusted": True},
                "fx_rate": {"value": 4.72, "source": "ecb_live",
                            "asof": "2026-05-20", "trusted": True},
                "fee": {"value": 23.6, "fee_pct": 0.005,
                        "source": "config:BANK_FEES.Maybank", "trusted": True},
                "actual_received": {"value": 4696.40,
                                    "source": "bank_statement:txn_t_1",
                                    "trusted": True},
                "verifier": {"ran": True, "verdict": "confirm", "concerns": [],
                             "method": "programmatic_skeptic_v1"},
                "all_inputs_trusted": True,
            }
        },
        "confidence": 0.99, "reasoning": "test", "status": "matched",
    }


def test_attestation_signs_and_verifies():
    """F12: signed manifest round-trips through verify_signature."""
    sha = _stash_proof_bytes()
    match = _basic_match(sha)
    att = attestation.attest(match, "Maybank", recon_id="r1", match_index=0)
    assert att["algorithm"] == "Ed25519"
    assert att["public_key_fingerprint"]
    assert attestation.verify_signature(att["manifest"], att["signature_b64"]) is True

    # Tamper with the manifest: signature must fail.
    forged = {**att["manifest"], "txn_id": "txn_evil"}
    assert attestation.verify_signature(forged, att["signature_b64"]) is False


def test_audit_pack_renders_with_attestation_page():
    """PDF builds end-to-end and is meaningfully larger than the pre-F12
    version (attestation page adds the manifest table + signature block).
    Reading the fingerprint out of compressed PDF streams is fragile, so we
    test the attestation contract separately in the other tests."""
    sha = _stash_proof_bytes()
    match = _basic_match(sha)
    pdf = build_audit_pack(match, "Maybank", recon_id="r2", match_index=0)
    assert pdf.startswith(b"%PDF-")
    # Pre-F12 audit packs ran ~3-4 KB; adding the attestation page roughly
    # doubles that. 5000 is a comfortable floor that catches a missing block.
    assert len(pdf) > 5000, f"PDF is suspiciously small ({len(pdf)} bytes); attestation page may be missing"


def test_source_bytes_tamper_blocks_audit_pack():
    """F11: if the on-disk bytes for the source SHA differ from the recorded
    SHA, build_audit_pack must refuse."""
    sha = _stash_proof_bytes(b"original-bytes")
    # Mutate the on-disk file via the recorded path
    rec = db.find_upload_by_sha(sha)
    Path(rec["storage_path"]).write_bytes(b"tampered-bytes")

    match = _basic_match(sha)
    with pytest.raises(attestation.SourceBytesTampered):
        build_audit_pack(match, "Maybank", recon_id="r3", match_index=0)


def test_public_key_endpoint_exposes_fingerprint():
    r = client.get("/api/audit-pack/public-key")
    assert r.status_code == 200
    j = r.json()
    assert j["algorithm"] == "Ed25519"
    assert len(j["fingerprint"]) == 16
    # Decoded raw key should be exactly 32 bytes (Ed25519 spec)
    raw = base64.b64decode(j["raw_b64"])
    assert len(raw) == 32


def test_verify_endpoint_accepts_signed_manifest_rejects_forgery():
    sha = _stash_proof_bytes()
    match = _basic_match(sha)
    att = attestation.attest(match, "Maybank", recon_id="r4", match_index=0)

    # Valid round-trip
    r1 = client.post("/api/audit-pack/verify", json={
        "manifest": att["manifest"], "signature_b64": att["signature_b64"],
    })
    assert r1.status_code == 200
    j1 = r1.json()
    assert j1["signature_valid"] is True
    assert j1["all_valid"] is True

    # Forge manifest: signature won't verify
    forged = {**att["manifest"], "txn_id": "txn_someone_else"}
    r2 = client.post("/api/audit-pack/verify", json={
        "manifest": forged, "signature_b64": att["signature_b64"],
    })
    assert r2.status_code == 200
    j2 = r2.json()
    assert j2["signature_valid"] is False
    assert j2["all_valid"] is False
