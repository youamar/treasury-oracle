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


def test_tenant_notes_round_trip():
    """Notes upsert per tenant; previous content goes to history on change."""
    from app import db
    with db.tenant_scope("notes-test"):
        # First read on fresh tenant returns empty.
        n0 = db.get_tenant_notes()
        assert n0["content"] == ""
        assert n0["updated_at"] is None

        db.save_tenant_notes("# Acme Corp\nPays from holding co.")
        n1 = db.get_tenant_notes()
        assert "Acme" in n1["content"]
        assert n1["updated_at"] is not None

        # History is empty on first save (no prior content to archive).
        assert db.list_tenant_notes_history() == []

        # Change → previous version goes to history.
        db.save_tenant_notes("# Acme Corp\nUPDATED.")
        n2 = db.get_tenant_notes()
        assert "UPDATED" in n2["content"]
        hist = db.list_tenant_notes_history()
        assert len(hist) == 1
        assert "Pays from holding co" in hist[0]["content"]


def test_tenant_notes_scoped_per_tenant():
    """Notes never leak between tenants."""
    from app import db
    with db.tenant_scope("acme"):
        db.save_tenant_notes("acme secrets")
    with db.tenant_scope("globex"):
        db.save_tenant_notes("globex secrets")
    with db.tenant_scope("acme"):
        assert "acme" in db.get_tenant_notes()["content"]
    with db.tenant_scope("globex"):
        assert "globex" in db.get_tenant_notes()["content"]


def test_notes_endpoints():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)

    # GET on empty tenant returns empty content
    r = client.get("/api/memory/notes", headers={"x-tenant-id": "ep-test"})
    assert r.status_code == 200
    assert r.json()["content"] == ""

    # PUT saves
    r = client.put("/api/memory/notes",
                   json={"content": "## Maybank quirks\nThey post next-day."},
                   headers={"x-tenant-id": "ep-test"})
    assert r.status_code == 200
    assert "Maybank" in r.json()["content"]

    # Summary reflects the write
    s = client.get("/api/memory/summary",
                   headers={"x-tenant-id": "ep-test"}).json()
    assert s["notes_chars"] > 0


def test_agent_reads_notes_into_system_prompt(sample_proof, sample_txn, monkeypatch):
    """Notes content shows up in the agent's system message."""
    from app import agent as agent_mod, db
    db.save_tenant_notes(
        "TENANT_NOTES_MARKER_XYZ123\nAcme always pays from Singapore.")

    captured = []
    class _Capture:
        class chat:
            class completions:
                @staticmethod
                def create(messages=None, **kw):
                    captured.extend(messages or [])
                    import json
                    class _M:
                        content = json.dumps({"decision": "no_match",
                                              "confidence": 0, "reasoning": "x"})
                        tool_calls = None
                    class _R: choices = [type("C", (), {"message": _M})]
                    return _R()
    monkeypatch.setattr(agent_mod, "get_client", lambda use_fallback=False: _Capture())

    agent_mod.reconcile_agent([sample_proof], [sample_txn], "Maybank")
    sys_msgs = [m for m in captured if m.get("role") == "system"]
    assert any("TENANT_NOTES_MARKER_XYZ123" in (m.get("content") or "")
               for m in sys_msgs), "tenant notes not threaded into agent system prompt"
