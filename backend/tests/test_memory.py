"""Memory layer: facts, aliases, uploads, multi-tenant isolation."""
import io
import hashlib
from fastapi.testclient import TestClient

from app.main import app
from app import db
from app import uploads as _uploads


client = TestClient(app)


# ---------- facts ----------

def test_remember_and_recall_fact():
    db.remember_fact("Acme Corp", "pays_late_by_days", "5", source="test")
    facts = db.recall_facts(subject="Acme")
    assert len(facts) == 1
    assert facts[0]["value"] == "5"


def test_remember_fact_is_upsert():
    db.remember_fact("BankX", "fee_pct", "0.010", source="t1")
    db.remember_fact("BankX", "fee_pct", "0.012", source="t2")
    facts = db.recall_facts(subject="BankX", predicate="fee_pct")
    assert len(facts) == 1
    assert facts[0]["value"] == "0.012"
    assert facts[0]["source"] == "t2"


def test_recall_facts_substring():
    db.remember_fact("Acme Corp", "preferred_route", "DBS", source="t")
    db.remember_fact("Acme Corp", "currency_default", "USD", source="t")
    facts = db.recall_facts(subject="Acme")
    assert {f["predicate"] for f in facts} == {"preferred_route", "currency_default"}


# ---------- tenant isolation ----------

def test_tenant_isolation_facts():
    with db.tenant_scope("tenant_a"):
        db.remember_fact("X", "p", "from_a", source="a")
    with db.tenant_scope("tenant_b"):
        db.remember_fact("X", "p", "from_b", source="b")
        assert db.recall_facts(subject="X")[0]["value"] == "from_b"
    with db.tenant_scope("tenant_a"):
        assert db.recall_facts(subject="X")[0]["value"] == "from_a"


def test_tenant_isolation_aliases():
    with db.tenant_scope("tenant_a"):
        db.remember_alias("Acme", "ACME CRP")
        assert db.all_aliases() == {"Acme": "ACME CRP"}
    with db.tenant_scope("tenant_b"):
        assert db.all_aliases() == {}


# ---------- uploads ----------

def test_upload_stores_and_dedupes():
    data = b"hello world"
    sha = hashlib.sha256(data).hexdigest()
    rec1 = _uploads.store_bytes("a.txt", data, purpose="test")
    rec2 = _uploads.store_bytes("a.txt", data, purpose="test")
    assert rec1["sha256"] == sha
    assert rec2["sha256"] == sha
    assert rec1["id"] == rec2["id"]  # dedup
    assert _uploads.read_bytes(sha) == data


def test_upload_tenant_scoped():
    data = b"tenant-scoped-bytes"
    with db.tenant_scope("tenant_a"):
        _uploads.store_bytes("x.bin", data, purpose="proof")
    with db.tenant_scope("tenant_b"):
        assert db.list_uploads() == []  # different tenant sees nothing


# ---------- memory API ----------

def test_memory_summary_endpoint():
    r = client.get("/api/memory/summary")
    assert r.status_code == 200
    data = r.json()
    assert "tenant" in data and "aliases" in data and "fact_count" in data


def test_memory_fact_crud_endpoint():
    r = client.post("/api/memory/facts", json={
        "subject": "TestSub", "predicate": "test_pred", "value": "v1",
    })
    assert r.status_code == 200
    fid = r.json()["fact"]["id"]
    r = client.get("/api/memory/facts?subject=TestSub")
    assert any(f["id"] == fid for f in r.json()["facts"])
    r = client.delete(f"/api/memory/facts/{fid}")
    assert r.status_code == 200


def test_memory_tenant_header_isolation():
    client.post("/api/memory/facts",
                json={"subject": "S", "predicate": "p", "value": "alpha"},
                headers={"X-Tenant-Id": "tenant_alpha"})
    client.post("/api/memory/facts",
                json={"subject": "S", "predicate": "p", "value": "beta"},
                headers={"X-Tenant-Id": "tenant_beta"})
    a = client.get("/api/memory/facts?subject=S",
                   headers={"X-Tenant-Id": "tenant_alpha"}).json()["facts"]
    b = client.get("/api/memory/facts?subject=S",
                   headers={"X-Tenant-Id": "tenant_beta"}).json()["facts"]
    assert any(f["value"] == "alpha" for f in a)
    assert all(f["value"] != "beta" for f in a)
    assert any(f["value"] == "beta" for f in b)


# ---------- memory skills are registered ----------

def test_memory_skills_registered():
    from app.skills import SKILL_REGISTRY
    assert "remember_fact" in SKILL_REGISTRY
    assert "recall_facts" in SKILL_REGISTRY
    assert SKILL_REGISTRY["remember_fact"].kind == "tool"


def test_recall_skill_handler_round_trip():
    from app.skills import SKILL_REGISTRY, SkillContext
    ctx = SkillContext(session_id="sess1", config={}, skill_config={})
    SKILL_REGISTRY["remember_fact"].handler(
        ctx, subject="RoundTrip", predicate="p", value="v",
    )
    out = SKILL_REGISTRY["recall_facts"].handler(ctx, subject="RoundTrip")
    assert out["count"] >= 1
    assert any(f["value"] == "v" for f in out["facts"])
