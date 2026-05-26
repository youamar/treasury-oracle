"""Raw upload storage — bytes on disk, metadata in SQLite.

Files live at:  backend/data/uploads/<tenant_id>/<sha256[:2]>/<sha256>
The same byte sequence is stored only once per tenant (dedup by SHA-256).
"""
from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path

from . import db


UPLOADS_DIR = Path(__file__).resolve().parent.parent / "data" / "uploads"

# Guardrails
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024     # 10 MB per file
MAX_BATCH_SIZE_BYTES = 50 * 1024 * 1024    # 50 MB per multi-file request

ALLOWED_PROOF_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".pdf"}
ALLOWED_STATEMENT_EXTS = {".csv", ".xlsx", ".xls"}
ALLOWED_AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".ogg"}


class UploadRejected(ValueError):
    """Raised when an upload violates a guardrail."""
    def __init__(self, status_code: int, reason: str):
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason


def validate_file(filename: str, size: int, allowed_exts: set[str]) -> None:
    if size > MAX_FILE_SIZE_BYTES:
        raise UploadRejected(413, f"{filename} exceeds {MAX_FILE_SIZE_BYTES // (1024*1024)} MB limit")
    ext = Path(filename).suffix.lower()
    if ext not in allowed_exts:
        raise UploadRejected(415, f"{filename}: extension {ext or '(none)'} not allowed")


def validate_batch(sizes: list[int]) -> None:
    total = sum(sizes)
    if total > MAX_BATCH_SIZE_BYTES:
        raise UploadRejected(413, f"batch size {total} exceeds {MAX_BATCH_SIZE_BYTES // (1024*1024)} MB")


def _tenant_dir(tenant: str) -> Path:
    p = UPLOADS_DIR / tenant
    p.mkdir(parents=True, exist_ok=True)
    return p


def store_bytes(filename: str, data: bytes,
                purpose: str | None = None,
                session_id: str | None = None,
                mime: str | None = None) -> dict:
    """Persist the raw bytes + return the recorded upload metadata."""
    sha = hashlib.sha256(data).hexdigest()
    tenant = db.current_tenant()
    target_dir = _tenant_dir(tenant) / sha[:2]
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / sha
    if not target.exists():
        target.write_bytes(data)
    if mime is None:
        mime = mimetypes.guess_type(filename)[0]
    return db.record_upload(
        filename=filename, sha256=sha, mime=mime, size=len(data),
        storage_path=str(target), purpose=purpose, session_id=session_id,
    )


def read_bytes(sha256: str) -> bytes | None:
    rec = db.find_upload_by_sha(sha256)
    if not rec:
        return None
    p = Path(rec["storage_path"])
    return p.read_bytes() if p.exists() else None
