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


# ---------- garbage collection (S-3) ----------

import time as _time

def gc_old_uploads(older_than_days: int = 30,
                   keep_sessions: bool = True) -> dict:
    """Delete raw_uploads rows older than the cutoff AND their bytes on disk.

    `keep_sessions=True` preserves any upload still referenced by a
    persisted session (the SHA is recorded in agent_trace + match
    provenance). Without that anchor, the audit-pack source-bytes
    verification would fail after GC.

    Returns a summary dict; safe to call from a cron/maintenance endpoint.
    """
    cutoff_epoch = _time.time() - older_than_days * 86400
    from datetime import datetime, timezone
    cutoff_iso = datetime.fromtimestamp(cutoff_epoch, timezone.utc).isoformat(timespec="seconds")

    deleted_rows = 0
    deleted_bytes = 0
    skipped_referenced = 0

    with db.conn() as c:
        # Find old rows. Multi-tenant safe: this is an unscoped maintenance
        # operation; only the orchestrator should call it.
        rows = c.execute(
            "SELECT id, sha256, storage_path, size FROM raw_uploads "
            "WHERE uploaded_at < ?",
            (cutoff_iso,),
        ).fetchall()

        for r in rows:
            if keep_sessions:
                # Is any agent_trace / match still pointing at this SHA?
                # Provenance carries it inside conversion_json; matches table
                # also stores the proof_json with source_sha256.
                referenced = c.execute(
                    "SELECT 1 FROM matches "
                    "WHERE proof_json LIKE ? OR conversion_json LIKE ? "
                    "LIMIT 1",
                    (f'%"{r["sha256"]}"%', f'%"{r["sha256"]}"%'),
                ).fetchone()
                if referenced:
                    skipped_referenced += 1
                    continue

            # Drop the row + the bytes on disk.
            c.execute("DELETE FROM raw_uploads WHERE id = ?", (r["id"],))
            try:
                p = Path(r["storage_path"])
                if p.exists():
                    deleted_bytes += p.stat().st_size
                    p.unlink()
            except Exception:
                pass
            deleted_rows += 1

    return {
        "cutoff": cutoff_iso,
        "rows_deleted": deleted_rows,
        "bytes_deleted": deleted_bytes,
        "rows_skipped_still_referenced": skipped_referenced,
    }
