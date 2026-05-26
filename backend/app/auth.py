"""Account + session auth for Treasury Oracle.

Replaces the email-only-login mode with proper credentials:

  * Passwords hashed with bcrypt (cost factor 12, random salt per row).
  * Sessions issued as signed tokens — base64url(payload) + "." +
    base64url(Ed25519(payload)), where payload is JSON
    {user_id, email, expires_at}. We re-use the existing audit-pack
    Ed25519 keypair as the platform's authority key — no new secret
    management surface.
  * Tokens carried by the client as Authorization: Bearer <token>.
  * The tenant_id contextvar is set from the verified token, NOT from
    a client-supplied x-tenant-id header (which was a real security
    hole — anyone could impersonate any tenant).

`users` table is scoped per-tenant because real-world tenants will want
distinct user pools (the Acme operator can't log in as the Globex CEO).
For the hackathon demo, the email's slug doubles as the tenant id so
each email creates its own tenant on first registration.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from typing import Optional

import bcrypt

from . import db, attestation


# ---------- table schema (added via init_db migration) ----------

SCHEMA_USERS = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    email TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT,
    role TEXT NOT NULL DEFAULT 'owner',
    created_at TEXT NOT NULL,
    last_login_at TEXT,
    UNIQUE (tenant_id, email)
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
"""


# ---------- helpers ----------

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(s: str) -> bool:
    return bool(_EMAIL_RE.match((s or "").strip()))


def tenant_id_from_email(email: str) -> str:
    """Slug the email for use as the tenant id when a new account is
    created. Predictable + URL-safe + readable."""
    e = email.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", e)
    return slug.strip("-")


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(12)).decode("ascii")


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"),
                              password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


# ---------- session tokens ----------

DEFAULT_SESSION_TTL_SECONDS = 7 * 24 * 3600  # one week


class AuthError(Exception):
    """Raised when a session token can't be validated. The caller renders
    this as 401."""


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("ascii"))


def issue_token(user_id: int, tenant_id: str, email: str,
                ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS) -> str:
    payload = {
        "v": 1,
        "uid": int(user_id),
        "tid": tenant_id,
        "em": email,
        "iat": int(time.time()),
        "exp": int(time.time()) + int(ttl_seconds),
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = attestation.sign(body)
    return f"{_b64url(body)}.{_b64url(sig)}"


def verify_token(token: str) -> dict:
    """Return the decoded payload if the token is valid, else raise AuthError."""
    if not token or "." not in token:
        raise AuthError("malformed token")
    body_b64, sig_b64 = token.split(".", 1)
    try:
        body = _b64url_decode(body_b64)
        sig = _b64url_decode(sig_b64)
    except Exception as e:
        raise AuthError(f"bad base64: {e}")
    if not attestation.verify(body, sig):
        raise AuthError("signature mismatch")
    try:
        payload = json.loads(body)
    except Exception:
        raise AuthError("malformed payload")
    if payload.get("exp", 0) < time.time():
        raise AuthError("token expired")
    return payload


# ---------- user CRUD ----------

def register_user(email: str, password: str,
                  display_name: Optional[str] = None) -> dict:
    """Create a new account. Each email maps to its own tenant; calling
    twice with the same email + matching password is treated as login."""
    email = (email or "").strip().lower()
    if not is_valid_email(email):
        raise AuthError("invalid email format")
    if not password or len(password) < 8:
        raise AuthError("password must be at least 8 characters")

    tid = tenant_id_from_email(email)
    pw_hash = hash_password(password)
    now = db._now()

    # Ensure the tenant exists.
    db.upsert_tenant(tid, display_name or email.split("@")[0])

    with db.tenant_scope(tid):
        with db.conn() as c:
            existing = c.execute(
                "SELECT id, password_hash FROM users WHERE tenant_id = ? AND email = ?",
                (tid, email),
            ).fetchone()
            if existing:
                # Idempotent registration: same password = login; mismatch = error.
                if not verify_password(password, existing["password_hash"]):
                    raise AuthError("email already registered with a different password")
                user_id = existing["id"]
            else:
                cur = c.execute(
                    "INSERT INTO users(tenant_id, email, password_hash, "
                    "display_name, role, created_at) VALUES (?,?,?,?,?,?)",
                    (tid, email, pw_hash, display_name, "owner", now),
                )
                user_id = cur.lastrowid

    token = issue_token(user_id, tid, email)
    return {"user_id": user_id, "tenant_id": tid, "email": email,
            "token": token, "expires_in": DEFAULT_SESSION_TTL_SECONDS}


def login(email: str, password: str) -> dict:
    email = (email or "").strip().lower()
    if not is_valid_email(email):
        raise AuthError("invalid email format")
    tid = tenant_id_from_email(email)

    with db.tenant_scope(tid):
        with db.conn() as c:
            row = c.execute(
                "SELECT id, password_hash FROM users WHERE tenant_id = ? AND email = ?",
                (tid, email),
            ).fetchone()
            if row is None or not verify_password(password, row["password_hash"]):
                raise AuthError("invalid credentials")
            c.execute("UPDATE users SET last_login_at = ? WHERE id = ?",
                      (db._now(), row["id"]))
            user_id = row["id"]

    token = issue_token(user_id, tid, email)
    return {"user_id": user_id, "tenant_id": tid, "email": email,
            "token": token, "expires_in": DEFAULT_SESSION_TTL_SECONDS}


def get_user_by_id(user_id: int) -> dict | None:
    with db.conn() as c:
        r = c.execute(
            "SELECT id, tenant_id, email, display_name, role, created_at, last_login_at "
            "FROM users WHERE id = ?", (user_id,),
        ).fetchone()
    return dict(r) if r else None


def init_auth_schema() -> None:
    """Idempotent — call from db.init_db()."""
    with db.conn() as c:
        c.executescript(SCHEMA_USERS)
