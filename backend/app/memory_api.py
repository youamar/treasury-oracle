"""Memory API — surface what the platform has learned for the current tenant."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from . import db
from . import uploads as _uploads
from . import reliability


router = APIRouter(prefix="/api/memory", tags=["memory"])


# ---------- aggregate ----------

@router.get("/summary")
def memory_summary():
    return {
        "tenant": db.current_tenant(),
        "aliases": db.all_aliases(),
        "fact_count": len(db.list_facts(limit=1000)),
        "session_count": len(db.list_sessions(limit=1000)),
        "upload_count": len(db.list_uploads(limit=1000)),
        "error_count": len(db.list_errors(limit=1000)),
    }


# ---------- aliases ----------

class AliasBody(BaseModel):
    canonical: str
    observed: str


@router.get("/aliases")
def list_aliases():
    return {"aliases": db.all_aliases()}


@router.put("/aliases")
def put_alias(body: AliasBody):
    db.remember_alias(body.canonical, body.observed)
    return {"ok": True, "aliases": db.all_aliases()}


@router.delete("/aliases/{canonical}")
def del_alias(canonical: str):
    db.delete_alias(canonical)
    return {"ok": True}


# ---------- facts ----------

class FactBody(BaseModel):
    subject: str
    predicate: str
    value: str
    confidence: float = 1.0
    source: str | None = "user"


@router.get("/facts")
def list_facts(subject: str | None = None, predicate: str | None = None):
    if subject or predicate:
        rows = db.recall_facts(subject=subject, predicate=predicate, limit=200)
    else:
        rows = db.list_facts(limit=200)
    return {"facts": rows}


@router.post("/facts")
def add_fact(body: FactBody):
    rec = db.remember_fact(
        subject=body.subject, predicate=body.predicate, value=body.value,
        source=body.source, confidence=body.confidence,
    )
    return {"ok": True, "fact": rec}


@router.delete("/facts/{fact_id}")
def del_fact(fact_id: int):
    db.delete_fact(fact_id)
    return {"ok": True}


# ---------- sessions ----------

@router.get("/sessions")
def memory_sessions(limit: int = 50):
    return {"sessions": db.list_sessions(limit=limit)}


# ---------- raw uploads ----------

@router.get("/uploads")
def list_uploads(session_id: str | None = None):
    return {"uploads": db.list_uploads(limit=200, session_id=session_id)}


@router.get("/uploads/{sha256}")
def get_upload(sha256: str):
    rec = db.find_upload_by_sha(sha256)
    if not rec:
        raise HTTPException(404, "upload not found")
    data = _uploads.read_bytes(sha256)
    if data is None:
        raise HTTPException(404, "bytes not on disk")
    return Response(
        content=data, media_type=rec.get("mime") or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{rec["filename"]}"'},
    )


# ---------- tenants ----------

class TenantBody(BaseModel):
    id: str
    name: str


@router.get("/tenants")
def get_tenants():
    return {"current": db.current_tenant(), "tenants": db.list_tenants()}


@router.post("/tenants")
def create_tenant(body: TenantBody):
    return db.upsert_tenant(body.id, body.name)


# ---------- errors ----------

@router.get("/errors")
def list_errors(limit: int = 50, source: str | None = None):
    return {"errors": db.list_errors(limit=limit, source=source)}


@router.delete("/errors")
def clear_errors():
    return {"cleared": db.clear_errors()}


# ---------- circuit breakers ----------

@router.get("/breakers")
def list_breakers():
    return {"breakers": reliability.breaker_snapshot()}


@router.post("/breakers/reset")
def reset_breakers(source: str | None = None):
    reliability.reset_breaker(source)
    return {"ok": True, "reset": source or "all"}


# ---------- live fixtures (auto-grown eval set) ----------

@router.get("/live-fixtures")
def list_live_fixtures():
    return {"live_fixtures": db.list_live_fixtures(limit=500)}


@router.delete("/live-fixtures/{fixture_id}")
def delete_live_fixture(fixture_id: str):
    db.delete_live_fixture(fixture_id)
    return {"ok": True}
