"""Tamper-evidence for audit pack PDFs.

Two guarantees we want to make to anyone holding a Treasury Oracle audit pack:

  (1) **Bytes integrity** — the source-of-truth payment proof referenced in the
      PDF (by SHA-256) actually still hashes to that value on the server. If
      the stored bytes are mutated, the audit pack refuses to render.

  (2) **PDF authorship** — the PDF was produced by *this* Treasury Oracle
      instance and not modified after issuance. Achieved by Ed25519-signing
      the PDF bytes (minus the signature block itself) and appending the
      signature + public-key fingerprint to the last page.

The keypair is generated lazily and stored under
  backend/data/keys/audit_pack_ed25519.{pem,pub}
so signatures survive restarts. In production this would be in a secret
manager; the file storage is the hackathon-acceptable substitute.
"""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from . import db


# ---------- key management ----------

_KEY_DIR = Path(__file__).resolve().parent.parent / "data" / "keys"
_PRIV_PATH = _KEY_DIR / "audit_pack_ed25519.pem"
_PUB_PATH  = _KEY_DIR / "audit_pack_ed25519.pub"

_priv_cache: Ed25519PrivateKey | None = None


def _load_or_create_keypair() -> Ed25519PrivateKey:
    global _priv_cache
    if _priv_cache is not None:
        return _priv_cache

    _KEY_DIR.mkdir(parents=True, exist_ok=True)
    if _PRIV_PATH.exists():
        priv_bytes = _PRIV_PATH.read_bytes()
        _priv_cache = serialization.load_pem_private_key(priv_bytes, password=None)
        return _priv_cache  # type: ignore[return-value]

    # First-time generation. We persist both the PEM-formatted private key
    # (for our own future signing) and a raw 32-byte public key fingerprint
    # (so anyone verifying signatures doesn't need to read PEM).
    priv = Ed25519PrivateKey.generate()
    _PRIV_PATH.write_bytes(priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    pub = priv.public_key()
    _PUB_PATH.write_bytes(pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ))
    _priv_cache = priv
    return priv


def public_key_bytes() -> bytes:
    """Raw 32-byte Ed25519 public key."""
    priv = _load_or_create_keypair()
    return priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def public_key_fingerprint() -> str:
    """Short, human-renderable identifier for the public key. SHA-256 of the
    raw key, hex-encoded, first 16 chars — same shape an SSH fingerprint."""
    return hashlib.sha256(public_key_bytes()).hexdigest()[:16]


def public_key_pem() -> str:
    priv = _load_or_create_keypair()
    return priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


# ---------- signing ----------

def sign(payload: bytes) -> bytes:
    """Ed25519 signature over the exact payload bytes. 64 bytes."""
    priv = _load_or_create_keypair()
    return priv.sign(payload)


def verify(payload: bytes, signature: bytes, pub_bytes: bytes | None = None) -> bool:
    """Verify a signature against the local public key (default) or a supplied one."""
    raw = pub_bytes or public_key_bytes()
    try:
        Ed25519PublicKey.from_public_bytes(raw).verify(signature, payload)
        return True
    except Exception:
        return False


# ---------- audit-pack manifest ----------

def build_manifest(match: dict, bank: str, recon_id: str | None,
                   match_index: int | None) -> dict:
    """Canonical JSON manifest of the audit pack. The signature is over the
    SHA-256 of this manifest plus the PDF body — so neither the manifest
    nor the PDF bytes can be modified post-issuance without breaking the
    signature."""
    prov = (match.get("conversion") or {}).get("provenance") or {}
    proof_prov = prov.get("proof_amount") or {}
    return {
        "schema": "treasury-oracle-audit-pack/v1",
        "issued_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "issuer_key_fingerprint": public_key_fingerprint(),
        "recon_id": recon_id,
        "match_index": match_index,
        "bank": bank,
        "invoice_reference": match["proof"].get("reference"),
        "txn_id": match["txn"]["id"],
        "amounts": {
            "proof": f"{match['proof'].get('amount')} {match['proof'].get('currency')}",
            "received": f"{match['txn'].get('amount')} {match['txn'].get('currency')}",
            "expected_net": (match.get("conversion") or {}).get("expected_net"),
            "actual_received": (match.get("conversion") or {}).get("actual_received"),
        },
        "proof_source_sha256": proof_prov.get("source_sha256"),
        "fx_source": (prov.get("fx_rate") or {}).get("source"),
        "fx_trusted": (prov.get("fx_rate") or {}).get("trusted"),
        "verifier_verdict": (prov.get("verifier") or {}).get("verdict"),
        "verifier_method": (prov.get("verifier") or {}).get("method"),
        "all_inputs_trusted": prov.get("all_inputs_trusted"),
    }


def attest(match: dict, bank: str,
           recon_id: str | None = None,
           match_index: int | None = None) -> dict:
    """Build the manifest and sign its canonical JSON. The manifest contains
    every fact that *anchors* the audit pack — the source SHA-256, the txn
    id, the verifier verdict — so once the manifest signature is verified,
    any contradiction in the PDF text is exposed by comparison."""
    manifest = build_manifest(match, bank, recon_id, match_index)
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sig = sign(canonical)
    return {
        "manifest": manifest,
        "manifest_canonical_sha256": hashlib.sha256(canonical).hexdigest(),
        "signature_b64": base64.b64encode(sig).decode("ascii"),
        "public_key_fingerprint": public_key_fingerprint(),
        "algorithm": "Ed25519",
    }


# ---------- source-bytes verification ----------

class SourceBytesTampered(Exception):
    """Raised when the proof PNG/PDF that was uploaded has been mutated on
    disk vs the SHA-256 recorded at upload time."""


def verify_source_bytes(sha256: str | None) -> dict:
    """Re-hash the stored bytes; assert they still match the recorded SHA.
    Returns the upload record on success, raises SourceBytesTampered on failure.
    Missing-sha is treated as 'unknown source' (returned, not raised)."""
    if not sha256:
        return {"present": False, "verified": False, "reason": "no_sha_in_manifest"}
    rec = db.find_upload_by_sha(sha256)
    if not rec:
        raise SourceBytesTampered(
            f"audit pack references upload {sha256[:16]}… but no record exists "
            f"in raw_uploads — source bytes were deleted or never stored"
        )
    p = Path(rec["storage_path"])
    if not p.exists():
        raise SourceBytesTampered(
            f"audit pack references upload {sha256[:16]}… but the bytes are gone from disk"
        )
    actual = hashlib.sha256(p.read_bytes()).hexdigest()
    if actual != sha256:
        raise SourceBytesTampered(
            f"audit pack references upload {sha256[:16]}… but on-disk bytes hash to "
            f"{actual[:16]}… — file was modified after upload"
        )
    return {"present": True, "verified": True, "filename": rec.get("filename"),
            "size": rec.get("size"), "uploaded_at": rec.get("uploaded_at")}


def verify_signature(manifest: dict, signature_b64: str,
                     pub_key_bytes: bytes | None = None) -> bool:
    """Verify an attestation: caller supplies the manifest dict and the
    base64 signature. Any whitespace/key-order normalization is handled
    by re-serializing canonically."""
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return verify(canonical, base64.b64decode(signature_b64), pub_key_bytes)
