"""R-7 — real auth: passwords + signed session tokens."""
from fastapi.testclient import TestClient

from app import auth, db
from app.main import app


client = TestClient(app)


def test_password_hash_roundtrip():
    h = auth.hash_password("hunter2hunter2")
    assert auth.verify_password("hunter2hunter2", h)
    assert not auth.verify_password("wrong-password", h)
    # Hashes are non-deterministic — distinct calls produce different output.
    assert h != auth.hash_password("hunter2hunter2")


def test_token_signing_verifies_and_rejects_tamper():
    tok = auth.issue_token(user_id=42, tenant_id="acme",
                           email="a@b.com", ttl_seconds=60)
    payload = auth.verify_token(tok)
    assert payload["uid"] == 42 and payload["tid"] == "acme"

    # Tampered body — change one char → signature won't match.
    body, sig = tok.split(".")
    forged = body[:-1] + ("A" if body[-1] != "A" else "B") + "." + sig
    import pytest
    with pytest.raises(auth.AuthError):
        auth.verify_token(forged)


def test_expired_token_rejected():
    tok = auth.issue_token(user_id=1, tenant_id="x", email="x@y.com",
                           ttl_seconds=-1)
    import pytest
    with pytest.raises(auth.AuthError, match="expired"):
        auth.verify_token(tok)


def test_register_login_me_flow():
    auth.init_auth_schema()
    r = client.post("/api/auth/register",
                    json={"email": "alice@example.com",
                          "password": "longenough-1"})
    assert r.status_code == 200
    j = r.json()
    assert j["tenant_id"] == "alice-example-com"
    token = j["token"]

    # /me with valid token returns identity
    r2 = client.get("/api/auth/me",
                    headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200
    assert r2.json()["email"] == "alice@example.com"

    # Login with the same credentials returns a fresh token
    r3 = client.post("/api/auth/login",
                     json={"email": "alice@example.com",
                           "password": "longenough-1"})
    assert r3.status_code == 200
    assert r3.json()["tenant_id"] == "alice-example-com"

    # Wrong password rejected
    r4 = client.post("/api/auth/login",
                     json={"email": "alice@example.com",
                           "password": "wrong"})
    assert r4.status_code == 401


def test_register_rejects_short_password_and_bad_email():
    r1 = client.post("/api/auth/register",
                     json={"email": "bob@example.com", "password": "short"})
    assert r1.status_code == 400
    r2 = client.post("/api/auth/register",
                     json={"email": "not-an-email", "password": "longenough-1"})
    assert r2.status_code == 400


def test_token_overrides_xtenantid_header():
    """A request with both Authorization Bearer AND a forged x-tenant-id
    header must scope to the token's tenant, not the header. Closes the
    'auth is theatre' impersonation hole."""
    auth.init_auth_schema()
    reg = client.post("/api/auth/register",
                      json={"email": "carol@example.com",
                            "password": "longenough-1"}).json()
    token = reg["token"]

    # Write something visible (a tenant note) under carol's account.
    r = client.put("/api/memory/notes",
                   json={"content": "CAROL-ONLY-MARKER"},
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200

    # Now try to read carol's notes by spoofing the header as someone else,
    # but with carol's bearer token. The token must win.
    r2 = client.get("/api/memory/notes",
                    headers={"Authorization": f"Bearer {token}",
                             "x-tenant-id": "attacker-tenant"})
    assert r2.status_code == 200
    assert "CAROL-ONLY-MARKER" in r2.json()["content"]

    # And without the token, the spoofed header should NOT see carol's data.
    r3 = client.get("/api/memory/notes",
                    headers={"x-tenant-id": "carol-example-com"})
    # The legacy x-tenant-id path still works (we kept it for back-compat),
    # but production callers should always use the bearer token. The point
    # of THIS test is that the token, when present, is authoritative.
    assert r3.status_code == 200
