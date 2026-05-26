"""SQLite persistence layer with multi-tenant scoping + memory layer.

Single-file DB at backend/data/treasury.db. Every row is scoped by tenant_id
(defaults to 'default'). The active tenant is held in a contextvar so existing
call sites do not need an explicit tenant arg.

Tables:
  - tenants               registry of known tenants
  - sessions / matches / soft_matches / discrepancies / unmatched_txns
  - campaigns / watchers / payer_aliases / agent_trace
  - platform_config       (per-tenant skill platform config)
  - raw_uploads           original bytes + SHA-256 of every uploaded file
  - memory_facts          free-form (subject, predicate, value) the agent
                          writes and reads via the remember_fact / recall_facts skills
"""
import json
import sqlite3
import threading
import contextvars
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID


# ---------- safe JSON encoder ----------

def _json_default(obj):
    """Encoder that preserves type fidelity on round-trip.

    Critically, Decimal → float (not str) so reconciliation amounts come
    back as numbers, not strings. Previously `default=str` made any Decimal
    in upstream data deserialize as '4.72', breaking arithmetic downstream."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if isinstance(obj, bytes):
        # Bytes have no JSON-safe representation that survives round-trip.
        # If you need to persist binary, store via raw_uploads and reference the SHA.
        raise TypeError(
            f"bytes are not JSON-safe; use raw_uploads + SHA-256 reference"
        )
    # numpy types: float64/int64 etc. Only import lazily so we don't depend on numpy.
    cls_name = type(obj).__name__
    if cls_name in ("float64", "float32"):
        return float(obj)
    if cls_name in ("int64", "int32", "int16", "int8"):
        return int(obj)
    if cls_name in ("ndarray",):
        return list(obj)
    raise TypeError(f"Object of type {cls_name} is not JSON serializable")


def safe_dumps(obj, **kw) -> str:
    """json.dumps with the type-preserving encoder. Use everywhere we persist."""
    return json.dumps(obj, default=_json_default, **kw)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "treasury.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
_init_lock = threading.Lock()
_initialized = False

# ---------- tenant context ----------

_current_tenant: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_tenant", default="default"
)


def set_tenant(tenant_id: str) -> None:
    _current_tenant.set(tenant_id or "default")


def current_tenant() -> str:
    return _current_tenant.get()


@contextmanager
def tenant_scope(tenant_id: str):
    token = _current_tenant.set(tenant_id or "default")
    try:
        yield
    finally:
        _current_tenant.reset(token)


SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    bank TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    trace_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS idx_sessions_tenant ON sessions(tenant_id);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    match_index INTEGER NOT NULL,
    proof_json TEXT NOT NULL,
    txn_json TEXT NOT NULL,
    conversion_json TEXT NOT NULL,
    confidence REAL NOT NULL,
    reasoning TEXT NOT NULL,
    status TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_matches_session ON matches(session_id);
CREATE INDEX IF NOT EXISTS idx_matches_tenant ON matches(tenant_id);

CREATE TABLE IF NOT EXISTS soft_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    proof_json TEXT NOT NULL,
    txn_json TEXT NOT NULL,
    conversion_json TEXT NOT NULL,
    confidence REAL NOT NULL,
    signals_json TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    confirmed INTEGER NOT NULL DEFAULT 0,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_soft_session ON soft_matches(session_id);

CREATE TABLE IF NOT EXISTS discrepancies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    proof_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    closest_txn_json TEXT,
    swift_route_json TEXT,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_disc_session ON discrepancies(session_id);

CREATE TABLE IF NOT EXISTS unmatched_txns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    txn_json TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    client_name TEXT NOT NULL,
    invoice_ref TEXT NOT NULL,
    invoice_amount REAL NOT NULL,
    invoice_ccy TEXT NOT NULL,
    outstanding REAL NOT NULL,
    due_date TEXT NOT NULL,
    current_stage INTEGER NOT NULL,
    status TEXT NOT NULL,
    history_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS idx_campaigns_tenant ON campaigns(tenant_id);

CREATE TABLE IF NOT EXISTS watchers (
    id TEXT PRIMARY KEY,
    from_ccy TEXT NOT NULL,
    to_ccy TEXT NOT NULL,
    target_rate REAL NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'default'
);

CREATE TABLE IF NOT EXISTS payer_aliases (
    tenant_id TEXT NOT NULL DEFAULT 'default',
    canonical TEXT NOT NULL,
    observed TEXT NOT NULL,
    learned_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, canonical)
);

CREATE TABLE IF NOT EXISTS agent_trace (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    proof_source TEXT,
    step INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS idx_trace_session ON agent_trace(session_id);

CREATE TABLE IF NOT EXISTS raw_uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    session_id TEXT,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    mime TEXT,
    size INTEGER NOT NULL,
    storage_path TEXT NOT NULL,
    purpose TEXT,                 -- 'proof' | 'statement' | 'voice' | etc.
    uploaded_at TEXT NOT NULL,
    UNIQUE (tenant_id, sha256)
);
CREATE INDEX IF NOT EXISTS idx_uploads_session ON raw_uploads(session_id);
CREATE INDEX IF NOT EXISTS idx_uploads_sha ON raw_uploads(sha256);

CREATE TABLE IF NOT EXISTS memory_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value TEXT NOT NULL,
    source TEXT,                  -- 'agent' | 'user' | session_id
    confidence REAL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    UNIQUE (tenant_id, subject, predicate)
);
CREATE INDEX IF NOT EXISTS idx_facts_subject ON memory_facts(tenant_id, subject);
CREATE INDEX IF NOT EXISTS idx_facts_predicate ON memory_facts(tenant_id, predicate);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    source TEXT NOT NULL,          -- e.g. 'chutes_client.chat', 'agent.run_one', 'upload'
    kind TEXT NOT NULL,            -- exception class name
    message TEXT NOT NULL,
    context_json TEXT NOT NULL,    -- arbitrary JSON
    traceback TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_errors_tenant ON errors(tenant_id);
CREATE INDEX IF NOT EXISTS idx_errors_created ON errors(created_at);

CREATE TABLE IF NOT EXISTS prompt_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    skill_id TEXT NOT NULL,
    version_hash TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    edited_by TEXT,                -- 'user' | 'wizard' | 'default'
    created_at TEXT NOT NULL,
    UNIQUE (tenant_id, skill_id, version_hash)
);
CREATE INDEX IF NOT EXISTS idx_prompt_versions_skill
    ON prompt_versions(tenant_id, skill_id, created_at);

CREATE TABLE IF NOT EXISTS agent_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    session_id TEXT NOT NULL,
    proof_index INTEGER,
    step INTEGER,
    skill_id TEXT,                 -- NULL = LLM call (no skill); else the dispatched skill
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    tokens_cached INTEGER DEFAULT 0,
    latency_ms REAL DEFAULT 0,
    status TEXT,                   -- 'ok' | 'error' | 'breaker_open'
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_metrics_session ON agent_metrics(session_id);
CREATE INDEX IF NOT EXISTS idx_agent_metrics_tenant ON agent_metrics(tenant_id);

CREATE TABLE IF NOT EXISTS eval_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    label TEXT,
    config_snapshot_json TEXT NOT NULL,    -- platform_config used for the run
    prompt_versions_json TEXT NOT NULL,    -- {skill_id: version_hash}
    metrics_json TEXT NOT NULL,            -- top-level aggregate metrics
    cases_json TEXT NOT NULL,              -- per-case verdicts
    duration_ms REAL NOT NULL,
    n_cases INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eval_runs_tenant ON eval_runs(tenant_id, created_at);

CREATE TABLE IF NOT EXISTS calibrators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    scope TEXT NOT NULL,           -- 'global' or '<decision_class>'
    method TEXT NOT NULL,          -- 'isotonic' | 'identity'
    coefficients_json TEXT NOT NULL,
    n_samples INTEGER NOT NULL,
    brier_before REAL,
    brier_after REAL,
    source_run_id INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE (tenant_id, scope)
);

CREATE TABLE IF NOT EXISTS tenant_notes (
    -- A free-form markdown 'knowledge file' per tenant. Read by the agent
    -- on every reconciliation, editable from the Memory page. Think of it
    -- as MEMORY.md scoped to one customer.
    tenant_id TEXT PRIMARY KEY,
    content TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tenant_notes_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    content TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    saved_by TEXT             -- 'user' | 'agent' | <session_id>
);
CREATE INDEX IF NOT EXISTS idx_notes_history_tenant
    ON tenant_notes_history(tenant_id, saved_at);

CREATE TABLE IF NOT EXISTS column_mappings (
    tenant_id TEXT NOT NULL DEFAULT 'default',
    bank TEXT NOT NULL,
    headers_json TEXT NOT NULL,         -- list[str] from the last successful parse
    columns_detected_json TEXT NOT NULL, -- {date, amount, currency, ...}
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, bank)
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    tenant_id TEXT NOT NULL DEFAULT 'default',
    key TEXT NOT NULL,
    request_hash TEXT NOT NULL,    -- sha256 of normalized request body
    recon_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, key)
);
CREATE INDEX IF NOT EXISTS idx_idempotency_created ON idempotency_keys(created_at);

CREATE TABLE IF NOT EXISTS live_fixtures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    fixture_id TEXT NOT NULL,
    bank TEXT,
    proof_json TEXT NOT NULL,
    txn_candidates_json TEXT NOT NULL,
    expected_decision TEXT NOT NULL,
    expected_txn_id TEXT,
    source TEXT,                   -- 'soft_match_confirm' | 'soft_match_reject' | 'manual'
    session_id TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (tenant_id, fixture_id)
);
CREATE INDEX IF NOT EXISTS idx_live_fixtures_tenant ON live_fixtures(tenant_id);
"""


# Tables that need tenant_id added if upgrading an existing DB.
_TENANT_MIGRATE_TABLES = [
    "sessions", "matches", "soft_matches", "discrepancies", "unmatched_txns",
    "campaigns", "watchers", "payer_aliases", "agent_trace",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _column_exists(c, table: str, col: str) -> bool:
    rows = c.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == col for r in rows)


def _migrate_tenant_columns(c):
    for tbl in _TENANT_MIGRATE_TABLES:
        try:
            if not _column_exists(c, tbl, "tenant_id"):
                c.execute(
                    f"ALTER TABLE {tbl} ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
                )
        except sqlite3.OperationalError:
            # Table doesn't exist yet (fresh DB) — SCHEMA will create it correctly.
            pass


def init_db(path: Path | None = None):
    """Idempotent schema creation + migration. Safe to call on every startup.

    Order matters: existing DBs from earlier schema versions may have tables
    without tenant_id. SCHEMA's CREATE INDEX ON ...(tenant_id) statements
    crash if we run them before adding the column. So:
      1. Ensure the tenants registry exists (needed by the FK target).
      2. Migrate tenant_id onto any pre-existing tables that lack it.
      3. Run the full SCHEMA (CREATE TABLE / INDEX IF NOT EXISTS).
    """
    global _initialized
    with _init_lock:
        p = path or DB_PATH
        with sqlite3.connect(p) as c:
            c.executescript("PRAGMA journal_mode=WAL;")
            # Bootstrap the tenants table by itself — _migrate references nothing
            # else, but downstream INSERT INTO tenants needs it to exist.
            c.executescript("""
                CREATE TABLE IF NOT EXISTS tenants (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            _migrate_tenant_columns(c)
            c.executescript(SCHEMA)
            # Seed default tenant
            c.execute(
                "INSERT OR IGNORE INTO tenants(id, name, created_at) VALUES (?, ?, ?)",
                ("default", "Default Tenant", _now()),
            )
            c.commit()
        _initialized = True
        # If callers switched DB_PATH (tests do this), drop pooled connections
        # so the next conn() opens against the new file.
        reset_pool()


# ---------- per-thread connection pool (R2) ----------
#
# A new sqlite3.connect() costs ~1ms and serializes against the WAL writer.
# Under the 4-way OCR fan-out the old "open per-call" pattern hit "database
# is locked" once concurrency crossed ~20 req/s. We now keep one connection
# per thread, reused for the thread's lifetime, with busy_timeout=30s so
# concurrent writers wait instead of crashing.

_tlocal = threading.local()


def _open_conn() -> sqlite3.Connection:
    c = sqlite3.connect(
        DB_PATH,
        timeout=30,
        isolation_level=None,           # autocommit; helpers explicitly BEGIN
        check_same_thread=False,        # safe — pool is per-thread anyway
    )
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("PRAGMA synchronous = NORMAL")     # WAL-safe, ~3x faster writes
    c.execute("PRAGMA busy_timeout = 30000")     # wait, don't crash, on lock
    return c


def reset_pool() -> None:
    """Drop pooled connections. Called when DB_PATH changes (tests) or on
    teardown. Best-effort — closing a connection from a thread that doesn't
    own it is undefined, so we only close the current thread's slot and let
    other threads pick up the new path lazily."""
    c = getattr(_tlocal, "conn", None)
    if c is not None:
        try:
            c.close()
        except Exception:
            pass
    _tlocal.conn = None
    _tlocal.path = None


@contextmanager
def conn():
    if not _initialized:
        init_db()
    c = getattr(_tlocal, "conn", None)
    # If the active DB_PATH changed (test isolation), drop and reopen.
    current_path = getattr(_tlocal, "path", None)
    if c is None or current_path != str(DB_PATH):
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
        c = _open_conn()
        _tlocal.conn = c
        _tlocal.path = str(DB_PATH)
    yield c
    # Intentionally do NOT close — pooled per thread.


def _t() -> str:
    return current_tenant()


# ---------- tenants ----------

def upsert_tenant(tenant_id: str, name: str) -> dict:
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO tenants(id, name, created_at) VALUES (?, ?, "
            "COALESCE((SELECT created_at FROM tenants WHERE id = ?), ?))",
            (tenant_id, name, tenant_id, _now()),
        )
    return {"id": tenant_id, "name": name}


def list_tenants() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, name, created_at FROM tenants ORDER BY created_at"
        )]


# ---------- sessions ----------

def save_session(session_id: str, bank: str, result: dict):
    t = _t()
    with conn() as c:
        c.execute("BEGIN")
        c.execute(
            "INSERT OR REPLACE INTO sessions(id, bank, summary_json, trace_json, "
            "created_at, tenant_id) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, bank, json.dumps(result["summary"]),
             json.dumps(result.get("trace", [])), _now(), t),
        )
        for tbl in ("matches", "soft_matches", "discrepancies", "unmatched_txns"):
            c.execute(
                f"DELETE FROM {tbl} WHERE session_id = ? AND tenant_id = ?",
                (session_id, t),
            )

        for i, m in enumerate(result.get("matches", [])):
            c.execute(
                "INSERT INTO matches(session_id, match_index, proof_json, txn_json, "
                "conversion_json, confidence, reasoning, status, tenant_id) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (session_id, i, json.dumps(m["proof"]), json.dumps(m["txn"]),
                 json.dumps(m["conversion"]), m["confidence"], m["reasoning"],
                 m["status"], t),
            )
        for s in result.get("soft_matches", []):
            c.execute(
                "INSERT INTO soft_matches(session_id, proof_json, txn_json, "
                "conversion_json, confidence, signals_json, reasoning, tenant_id) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (session_id, json.dumps(s["proof"]), json.dumps(s["txn"]),
                 json.dumps(s["conversion"]), s["confidence"],
                 json.dumps(s.get("signals", [])), s["reasoning"], t),
            )
        for d in result.get("unmatched_proofs", []):
            c.execute(
                "INSERT INTO discrepancies(session_id, proof_json, reason, "
                "closest_txn_json, swift_route_json, tenant_id) VALUES (?,?,?,?,?,?)",
                (session_id, json.dumps(d), d.get("reason", ""),
                 json.dumps(d.get("closest_txn")) if d.get("closest_txn") else None,
                 json.dumps(d.get("swift_route")) if d.get("swift_route") else None,
                 t),
            )
        for tx in result.get("unmatched_txns", []):
            c.execute(
                "INSERT INTO unmatched_txns(session_id, txn_json, tenant_id) "
                "VALUES (?, ?, ?)",
                (session_id, json.dumps(tx), t),
            )
        c.execute("COMMIT")


def load_session(session_id: str) -> dict | None:
    t = _t()
    with conn() as c:
        s = c.execute(
            "SELECT * FROM sessions WHERE id = ? AND tenant_id = ?", (session_id, t)
        ).fetchone()
        if not s:
            return None
        matches = [_match_row(r) for r in c.execute(
            "SELECT * FROM matches WHERE session_id = ? AND tenant_id = ? "
            "ORDER BY match_index", (session_id, t))]
        soft = [_soft_row(r) for r in c.execute(
            "SELECT * FROM soft_matches WHERE session_id = ? AND tenant_id = ?",
            (session_id, t))]
        disc = [_disc_row(r) for r in c.execute(
            "SELECT * FROM discrepancies WHERE session_id = ? AND tenant_id = ?",
            (session_id, t))]
        unmatched_txns = [json.loads(r["txn_json"]) for r in c.execute(
            "SELECT * FROM unmatched_txns WHERE session_id = ? AND tenant_id = ?",
            (session_id, t))]
        trace = [json.loads(r["payload_json"]) for r in c.execute(
            "SELECT * FROM agent_trace WHERE session_id = ? AND tenant_id = ? "
            "ORDER BY step", (session_id, t))]
        return {
            "recon_id": session_id,
            "bank": s["bank"],
            "summary": json.loads(s["summary_json"]),
            "trace": json.loads(s["trace_json"]),
            "agent_trace": trace,
            "matches": matches,
            "soft_matches": soft,
            "unmatched_proofs": disc,
            "unmatched_txns": unmatched_txns,
        }


def list_sessions(limit: int = 50) -> list[dict]:
    with conn() as c:
        return [
            {"id": r["id"], "bank": r["bank"],
             "summary": json.loads(r["summary_json"]),
             "created_at": r["created_at"]}
            for r in c.execute(
                "SELECT id, bank, summary_json, created_at FROM sessions "
                "WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
                (_t(), limit),
            )
        ]


def _match_row(r):
    return {
        "proof": json.loads(r["proof_json"]),
        "txn": json.loads(r["txn_json"]),
        "conversion": json.loads(r["conversion_json"]),
        "confidence": r["confidence"],
        "reasoning": r["reasoning"],
        "status": r["status"],
    }


def _soft_row(r):
    return {
        "id": r["id"],
        "proof": json.loads(r["proof_json"]),
        "txn": json.loads(r["txn_json"]),
        "conversion": json.loads(r["conversion_json"]),
        "confidence": r["confidence"],
        "signals": json.loads(r["signals_json"]),
        "reasoning": r["reasoning"],
        "confirmed": bool(r["confirmed"]),
        "status": "soft_match_confirmed" if r["confirmed"] else "soft_match_pending",
    }


def _disc_row(r):
    d = json.loads(r["proof_json"])
    d["reason"] = r["reason"]
    if r["closest_txn_json"]:
        d["closest_txn"] = json.loads(r["closest_txn_json"])
    if r["swift_route_json"]:
        d["swift_route"] = json.loads(r["swift_route_json"])
    return d


def promote_soft_match(soft_match_id: int) -> dict | None:
    t = _t()
    with conn() as c:
        c.execute("BEGIN")
        s = c.execute(
            "SELECT * FROM soft_matches WHERE id = ? AND tenant_id = ?",
            (soft_match_id, t),
        ).fetchone()
        if not s:
            c.execute("ROLLBACK")
            return None
        c.execute("UPDATE soft_matches SET confirmed = 1 WHERE id = ?", (soft_match_id,))
        idx_row = c.execute(
            "SELECT COALESCE(MAX(match_index), -1) + 1 AS i FROM matches "
            "WHERE session_id = ? AND tenant_id = ?",
            (s["session_id"], t),
        ).fetchone()
        next_idx = idx_row["i"]
        c.execute(
            "INSERT INTO matches(session_id, match_index, proof_json, txn_json, "
            "conversion_json, confidence, reasoning, status, tenant_id) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (s["session_id"], next_idx, s["proof_json"], s["txn_json"],
             s["conversion_json"], s["confidence"], s["reasoning"],
             "matched_via_soft", t),
        )
        c.execute("COMMIT")
        return load_session(s["session_id"])


# ---------- agent trace ----------

def append_trace(session_id: str, proof_source: str | None, step: int,
                 type_: str, payload: dict):
    with conn() as c:
        c.execute(
            "INSERT INTO agent_trace(session_id, proof_source, step, type, "
            "payload_json, created_at, tenant_id) VALUES (?,?,?,?,?,?,?)",
            (session_id, proof_source, step, type_, json.dumps(payload),
             _now(), _t()),
        )


def get_trace(session_id: str) -> list[dict]:
    with conn() as c:
        return [
            {"step": r["step"], "type": r["type"], "proof_source": r["proof_source"],
             "payload": json.loads(r["payload_json"]), "at": r["created_at"]}
            for r in c.execute(
                "SELECT * FROM agent_trace WHERE session_id = ? AND tenant_id = ? "
                "ORDER BY id", (session_id, _t())
            )
        ]


# ---------- payer aliases ----------

def remember_alias(canonical: str, observed: str):
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO payer_aliases(tenant_id, canonical, observed, "
            "learned_at) VALUES (?, ?, ?, ?)",
            (_t(), canonical, observed, _now()),
        )


def lookup_alias(canonical: str) -> str | None:
    with conn() as c:
        r = c.execute(
            "SELECT observed FROM payer_aliases WHERE tenant_id = ? AND canonical = ?",
            (_t(), canonical),
        ).fetchone()
        return r["observed"] if r else None


def all_aliases() -> dict[str, str]:
    with conn() as c:
        return {r["canonical"]: r["observed"] for r in c.execute(
            "SELECT canonical, observed FROM payer_aliases WHERE tenant_id = ?",
            (_t(),),
        )}


def delete_alias(canonical: str):
    with conn() as c:
        c.execute(
            "DELETE FROM payer_aliases WHERE tenant_id = ? AND canonical = ?",
            (_t(), canonical),
        )


# ---------- campaigns ----------

def upsert_campaign(c_dict: dict):
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO campaigns(id, client_name, invoice_ref, "
            "invoice_amount, invoice_ccy, outstanding, due_date, current_stage, "
            "status, history_json, created_at, tenant_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (c_dict["id"], c_dict["client_name"], c_dict["invoice_ref"],
             c_dict["invoice_amount"], c_dict["invoice_ccy"], c_dict["outstanding"],
             c_dict["due_date"], c_dict["current_stage"], c_dict["status"],
             json.dumps(c_dict["history"]), c_dict.get("created_at", _now()), _t()),
        )


def get_campaign(cid: str) -> dict | None:
    with conn() as c:
        r = c.execute(
            "SELECT * FROM campaigns WHERE id = ? AND tenant_id = ?", (cid, _t())
        ).fetchone()
        return _campaign_row(r) if r else None


def list_campaigns_db() -> list[dict]:
    with conn() as c:
        return [_campaign_row(r) for r in c.execute(
            "SELECT * FROM campaigns WHERE tenant_id = ? ORDER BY created_at DESC",
            (_t(),))]


def _campaign_row(r):
    return {
        "id": r["id"], "client_name": r["client_name"], "invoice_ref": r["invoice_ref"],
        "invoice_amount": r["invoice_amount"], "invoice_ccy": r["invoice_ccy"],
        "outstanding": r["outstanding"], "due_date": r["due_date"],
        "current_stage": r["current_stage"], "status": r["status"],
        "history": json.loads(r["history_json"]), "created_at": r["created_at"],
    }


# ---------- watchers ----------

def upsert_watcher(w: dict):
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO watchers(id, from_ccy, to_ccy, target_rate, "
            "note, created_at, tenant_id) VALUES (?,?,?,?,?,?,?)",
            (w["id"], w["from_ccy"], w["to_ccy"], w["target_rate"],
             w.get("note", ""), w.get("created_at", _now()), _t()),
        )


def list_watchers() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM watchers WHERE tenant_id = ? ORDER BY created_at DESC",
            (_t(),))]


def delete_watcher(wid: str):
    with conn() as c:
        c.execute(
            "DELETE FROM watchers WHERE id = ? AND tenant_id = ?", (wid, _t())
        )


# ---------- raw uploads ----------

def record_upload(filename: str, sha256: str, mime: str | None, size: int,
                  storage_path: str, purpose: str | None,
                  session_id: str | None = None) -> dict:
    t = _t()
    with conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO raw_uploads(tenant_id, session_id, filename, "
            "sha256, mime, size, storage_path, purpose, uploaded_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (t, session_id, filename, sha256, mime, size, storage_path, purpose,
             _now()),
        )
        r = c.execute(
            "SELECT * FROM raw_uploads WHERE tenant_id = ? AND sha256 = ?",
            (t, sha256),
        ).fetchone()
    return dict(r) if r else {}


def find_upload_by_sha(sha256: str) -> dict | None:
    with conn() as c:
        r = c.execute(
            "SELECT * FROM raw_uploads WHERE tenant_id = ? AND sha256 = ?",
            (_t(), sha256),
        ).fetchone()
    return dict(r) if r else None


def list_uploads(limit: int = 100, session_id: str | None = None) -> list[dict]:
    with conn() as c:
        if session_id:
            rows = c.execute(
                "SELECT * FROM raw_uploads WHERE tenant_id = ? AND session_id = ? "
                "ORDER BY uploaded_at DESC LIMIT ?",
                (_t(), session_id, limit),
            )
        else:
            rows = c.execute(
                "SELECT * FROM raw_uploads WHERE tenant_id = ? "
                "ORDER BY uploaded_at DESC LIMIT ?",
                (_t(), limit),
            )
        return [dict(r) for r in rows]


# ---------- memory facts ----------

def remember_fact(subject: str, predicate: str, value: str,
                  source: str | None = None, confidence: float = 1.0) -> dict:
    t = _t()
    with conn() as c:
        c.execute(
            "INSERT INTO memory_facts(tenant_id, subject, predicate, value, "
            "source, confidence, created_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(tenant_id, subject, predicate) DO UPDATE SET "
            "value = excluded.value, source = excluded.source, "
            "confidence = excluded.confidence, created_at = excluded.created_at",
            (t, subject, predicate, value, source, confidence, _now()),
        )
        r = c.execute(
            "SELECT * FROM memory_facts WHERE tenant_id = ? AND subject = ? "
            "AND predicate = ?", (t, subject, predicate),
        ).fetchone()
    return dict(r) if r else {}


def recall_facts(subject: str | None = None, predicate: str | None = None,
                 limit: int = 20) -> list[dict]:
    """Keyword/substring search over (subject, predicate). Returns recent first."""
    t = _t()
    clauses = ["tenant_id = ?"]
    params: list = [t]
    if subject:
        clauses.append("subject LIKE ?")
        params.append(f"%{subject}%")
    if predicate:
        clauses.append("predicate LIKE ?")
        params.append(f"%{predicate}%")
    where = " AND ".join(clauses)
    params.append(limit)
    with conn() as c:
        rows = c.execute(
            f"SELECT * FROM memory_facts WHERE {where} "
            f"ORDER BY created_at DESC LIMIT ?", params,
        )
        return [dict(r) for r in rows]


def list_facts(limit: int = 200) -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM memory_facts WHERE tenant_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (_t(), limit),
        )]


def delete_fact(fact_id: int):
    with conn() as c:
        c.execute(
            "DELETE FROM memory_facts WHERE id = ? AND tenant_id = ?",
            (fact_id, _t()),
        )


# ---------- errors ----------

def record_error(source: str, kind: str, message: str,
                 context: dict | None = None,
                 traceback_text: str | None = None) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO errors(tenant_id, source, kind, message, context_json, "
            "traceback, created_at) VALUES (?,?,?,?,?,?,?)",
            (_t(), source, kind, message,
             safe_dumps(context or {}), traceback_text, _now()),
        )
        return cur.lastrowid


def list_errors(limit: int = 100, source: str | None = None) -> list[dict]:
    with conn() as c:
        if source:
            rows = c.execute(
                "SELECT * FROM errors WHERE tenant_id = ? AND source = ? "
                "ORDER BY id DESC LIMIT ?", (_t(), source, limit),
            )
        else:
            rows = c.execute(
                "SELECT * FROM errors WHERE tenant_id = ? "
                "ORDER BY id DESC LIMIT ?", (_t(), limit),
            )
        return [
            {**dict(r), "context": json.loads(r["context_json"] or "{}")}
            for r in rows
        ]


def clear_errors() -> int:
    with conn() as c:
        cur = c.execute("DELETE FROM errors WHERE tenant_id = ?", (_t(),))
        return cur.rowcount


# ---------- prompt versions ----------

def record_prompt_version(skill_id: str, prompt_text: str,
                          version_hash: str, edited_by: str = "user") -> dict:
    with conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO prompt_versions(tenant_id, skill_id, "
            "version_hash, prompt_text, edited_by, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (_t(), skill_id, version_hash, prompt_text, edited_by, _now()),
        )
        r = c.execute(
            "SELECT * FROM prompt_versions WHERE tenant_id = ? "
            "AND skill_id = ? AND version_hash = ?",
            (_t(), skill_id, version_hash),
        ).fetchone()
    return dict(r) if r else {}


def list_prompt_versions(skill_id: str | None = None, limit: int = 50) -> list[dict]:
    with conn() as c:
        if skill_id:
            rows = c.execute(
                "SELECT id, skill_id, version_hash, prompt_text, edited_by, created_at "
                "FROM prompt_versions WHERE tenant_id = ? AND skill_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (_t(), skill_id, limit),
            )
        else:
            rows = c.execute(
                "SELECT id, skill_id, version_hash, prompt_text, edited_by, created_at "
                "FROM prompt_versions WHERE tenant_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (_t(), limit),
            )
        return [dict(r) for r in rows]


# ---------- agent metrics ----------

def record_metric(session_id: str, *, proof_index: int | None = None,
                  step: int | None = None, skill_id: str | None = None,
                  tokens_in: int = 0, tokens_out: int = 0,
                  tokens_cached: int = 0, latency_ms: float = 0,
                  status: str = "ok") -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO agent_metrics(tenant_id, session_id, proof_index, step, "
            "skill_id, tokens_in, tokens_out, tokens_cached, latency_ms, "
            "status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (_t(), session_id, proof_index, step, skill_id, tokens_in,
             tokens_out, tokens_cached, latency_ms, status, _now()),
        )


def session_metrics(session_id: str) -> dict:
    with conn() as c:
        rows = list(c.execute(
            "SELECT skill_id, status, tokens_in, tokens_out, tokens_cached, "
            "latency_ms FROM agent_metrics WHERE session_id = ? AND tenant_id = ?",
            (session_id, _t()),
        ))
    total_in = sum(r["tokens_in"] for r in rows)
    total_out = sum(r["tokens_out"] for r in rows)
    total_cached = sum(r["tokens_cached"] for r in rows)
    total_latency = sum(r["latency_ms"] for r in rows)
    per_skill: dict[str, dict] = {}
    for r in rows:
        sk = r["skill_id"] or "<llm>"
        s = per_skill.setdefault(sk, {"calls": 0, "tokens_in": 0, "tokens_out": 0,
                                     "latency_ms": 0.0, "errors": 0})
        s["calls"] += 1
        s["tokens_in"] += r["tokens_in"]
        s["tokens_out"] += r["tokens_out"]
        s["latency_ms"] += r["latency_ms"]
        if r["status"] != "ok":
            s["errors"] += 1
    return {
        "total_tokens_in": total_in,
        "total_tokens_out": total_out,
        "total_tokens_cached": total_cached,
        "total_latency_ms": total_latency,
        "per_skill": per_skill,
        "n_steps": len(rows),
    }


def tenant_metrics_summary(limit_sessions: int = 200) -> dict:
    with conn() as c:
        rows = list(c.execute(
            "SELECT session_id, skill_id, tokens_in, tokens_out, latency_ms, status "
            "FROM agent_metrics WHERE tenant_id = ? ORDER BY id DESC LIMIT ?",
            (_t(), limit_sessions * 20),
        ))
    sessions: set[str] = set()
    total_in = 0
    total_out = 0
    total_latency = 0.0
    errors = 0
    for r in rows:
        sessions.add(r["session_id"])
        total_in += r["tokens_in"]
        total_out += r["tokens_out"]
        total_latency += r["latency_ms"]
        if r["status"] != "ok":
            errors += 1
    return {
        "n_sessions_sampled": len(sessions),
        "n_steps": len(rows),
        "total_tokens_in": total_in,
        "total_tokens_out": total_out,
        "total_latency_ms": total_latency,
        "error_steps": errors,
    }


# ---------- eval runs ----------

def save_eval_run(label: str, config_snapshot: dict, prompt_versions: dict,
                  metrics: dict, cases: list[dict], duration_ms: float) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO eval_runs(tenant_id, label, config_snapshot_json, "
            "prompt_versions_json, metrics_json, cases_json, duration_ms, "
            "n_cases, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (_t(), label,
             safe_dumps(config_snapshot),
             safe_dumps(prompt_versions),
             safe_dumps(metrics),
             safe_dumps(cases),
             duration_ms, len(cases), _now()),
        )
        return cur.lastrowid


def list_eval_runs(limit: int = 20) -> list[dict]:
    with conn() as c:
        rows = list(c.execute(
            "SELECT id, label, metrics_json, prompt_versions_json, duration_ms, "
            "n_cases, created_at FROM eval_runs WHERE tenant_id = ? "
            "ORDER BY id DESC LIMIT ?", (_t(), limit),
        ))
        return [
            {**dict(r),
             "metrics": json.loads(r["metrics_json"]),
             "prompt_versions": json.loads(r["prompt_versions_json"])}
            for r in rows
        ]


def get_eval_run(run_id: int) -> dict | None:
    with conn() as c:
        r = c.execute(
            "SELECT * FROM eval_runs WHERE id = ? AND tenant_id = ?",
            (run_id, _t()),
        ).fetchone()
    if not r:
        return None
    d = dict(r)
    return {
        **d,
        "config_snapshot": json.loads(d["config_snapshot_json"]),
        "prompt_versions": json.loads(d["prompt_versions_json"]),
        "metrics": json.loads(d["metrics_json"]),
        "cases": json.loads(d["cases_json"]),
    }


# ---------- calibrators ----------

def save_calibrator(scope: str, method: str, coefficients: dict,
                    n_samples: int, brier_before: float | None,
                    brier_after: float | None,
                    source_run_id: int | None = None) -> int:
    with conn() as c:
        c.execute(
            "INSERT INTO calibrators(tenant_id, scope, method, coefficients_json, "
            "n_samples, brier_before, brier_after, source_run_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(tenant_id, scope) DO UPDATE SET "
            "method=excluded.method, coefficients_json=excluded.coefficients_json, "
            "n_samples=excluded.n_samples, brier_before=excluded.brier_before, "
            "brier_after=excluded.brier_after, source_run_id=excluded.source_run_id, "
            "created_at=excluded.created_at",
            (_t(), scope, method, json.dumps(coefficients), n_samples,
             brier_before, brier_after, source_run_id, _now()),
        )
        r = c.execute(
            "SELECT id FROM calibrators WHERE tenant_id = ? AND scope = ?",
            (_t(), scope),
        ).fetchone()
        return r["id"] if r else 0


def load_calibrators() -> dict[str, dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM calibrators WHERE tenant_id = ?", (_t(),)
        ).fetchall()
    out = {}
    for r in rows:
        out[r["scope"]] = {
            **dict(r),
            "coefficients": json.loads(r["coefficients_json"]),
        }
    return out


def delete_calibrators():
    with conn() as c:
        c.execute("DELETE FROM calibrators WHERE tenant_id = ?", (_t(),))


# ---------- live fixtures ----------

def add_live_fixture(fixture_id: str, bank: str | None, proof: dict,
                     txn_candidates: list[dict], expected_decision: str,
                     expected_txn_id: str | None = None,
                     source: str | None = None,
                     session_id: str | None = None,
                     notes: str | None = None) -> dict:
    with conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO live_fixtures(tenant_id, fixture_id, bank, "
            "proof_json, txn_candidates_json, expected_decision, expected_txn_id, "
            "source, session_id, notes, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (_t(), fixture_id, bank, safe_dumps(proof),
             safe_dumps(txn_candidates),
             expected_decision, expected_txn_id, source, session_id,
             notes, _now()),
        )
        r = c.execute(
            "SELECT * FROM live_fixtures WHERE tenant_id = ? AND fixture_id = ?",
            (_t(), fixture_id),
        ).fetchone()
    return dict(r) if r else {}


def list_live_fixtures(limit: int = 200) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM live_fixtures WHERE tenant_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (_t(), limit),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["proof"] = json.loads(d["proof_json"])
        d["txn_candidates"] = json.loads(d["txn_candidates_json"])
        out.append(d)
    return out


# ---------- tenant notes (per-account knowledge file) ----------

def get_tenant_notes() -> dict:
    """Return the current notes for this tenant. Always returns a dict;
    `content` is empty string for first-time tenants."""
    with conn() as c:
        r = c.execute(
            "SELECT content, updated_at FROM tenant_notes WHERE tenant_id = ?",
            (_t(),),
        ).fetchone()
    if r is None:
        return {"content": "", "updated_at": None}
    return {"content": r["content"], "updated_at": r["updated_at"]}


def save_tenant_notes(content: str, saved_by: str = "user") -> dict:
    """Upsert the notes. Also pushes the previous version into a history
    table so accidental overwrites can be recovered."""
    t = _t()
    now = _now()
    with conn() as c:
        c.execute("BEGIN")
        prev = c.execute(
            "SELECT content FROM tenant_notes WHERE tenant_id = ?", (t,),
        ).fetchone()
        # Only record history if content actually changed.
        if prev is not None and prev["content"] != content:
            c.execute(
                "INSERT INTO tenant_notes_history(tenant_id, content, saved_at, saved_by) "
                "VALUES (?, ?, ?, ?)",
                (t, prev["content"], now, saved_by),
            )
        c.execute(
            "INSERT INTO tenant_notes(tenant_id, content, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(tenant_id) DO UPDATE SET "
            "content = excluded.content, updated_at = excluded.updated_at",
            (t, content, now),
        )
        c.execute("COMMIT")
    return {"content": content, "updated_at": now}


def list_tenant_notes_history(limit: int = 20) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT id, content, saved_at, saved_by FROM tenant_notes_history "
            "WHERE tenant_id = ? ORDER BY id DESC LIMIT ?",
            (_t(), limit),
        )
        return [dict(r) for r in rows]


# ---------- column mappings (F5: drift detection) ----------

def get_column_mapping(bank: str) -> dict | None:
    """Last-seen header set + resolved column mapping for this (tenant, bank)."""
    with conn() as c:
        r = c.execute(
            "SELECT headers_json, columns_detected_json, updated_at "
            "FROM column_mappings WHERE tenant_id = ? AND bank = ?",
            (_t(), bank),
        ).fetchone()
    if r is None:
        return None
    return {
        "headers": json.loads(r["headers_json"]),
        "columns_detected": json.loads(r["columns_detected_json"]),
        "updated_at": r["updated_at"],
    }


def remember_column_mapping(bank: str, headers: list[str],
                            columns_detected: dict) -> None:
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO column_mappings"
            "(tenant_id, bank, headers_json, columns_detected_json, updated_at) "
            "VALUES (?,?,?,?,?)",
            (_t(), bank, safe_dumps(headers), safe_dumps(columns_detected), _now()),
        )


def compute_column_drift(previous: dict | None, current_headers: list[str],
                         current_columns: dict) -> dict:
    """Compare current parse vs previous successful parse. Returns:
        {drift: bool, severity: 'none'|'headers_renamed'|'fields_moved', changes: [...]}
    """
    if previous is None:
        return {"drift": False, "severity": "none", "changes": [],
                "is_first_seen": True}

    changes: list[dict] = []
    prev_cols = previous.get("columns_detected") or {}
    # Compare every logical field
    for field, prev_col in prev_cols.items():
        cur_col = current_columns.get(field)
        if prev_col != cur_col:
            changes.append({
                "field": field,
                "previous_column": prev_col,
                "current_column": cur_col,
                "kind": ("disappeared" if cur_col is None
                         else "appeared" if prev_col is None
                         else "renamed"),
            })

    severity = "none"
    if changes:
        # If a *critical* field (date/amount) moved or disappeared, escalate.
        critical_fields = {"date", "amount"}
        critical_change = any(c["field"] in critical_fields for c in changes)
        severity = "fields_moved" if critical_change else "headers_renamed"

    return {
        "drift": bool(changes),
        "severity": severity,
        "changes": changes,
        "is_first_seen": False,
        "previous_updated_at": previous.get("updated_at"),
    }


# ---------- idempotency ----------

class IdempotencyConflict(Exception):
    """Same key was previously used with a different request body."""
    def __init__(self, key: str, existing_hash: str, new_hash: str):
        super().__init__(
            f"Idempotency-Key {key!r} was reused with a different request body"
        )
        self.key = key
        self.existing_hash = existing_hash
        self.new_hash = new_hash


def lookup_idempotency(key: str, request_hash: str) -> str | None:
    """Returns the recon_id previously associated with (tenant, key).
    Raises IdempotencyConflict if the key was used with a different body.
    Returns None if the key is unseen."""
    with conn() as c:
        r = c.execute(
            "SELECT request_hash, recon_id FROM idempotency_keys "
            "WHERE tenant_id = ? AND key = ?",
            (_t(), key),
        ).fetchone()
    if r is None:
        return None
    if r["request_hash"] != request_hash:
        raise IdempotencyConflict(key, r["request_hash"], request_hash)
    return r["recon_id"]


def store_idempotency(key: str, request_hash: str, recon_id: str) -> None:
    with conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO idempotency_keys"
            "(tenant_id, key, request_hash, recon_id, created_at) "
            "VALUES (?,?,?,?,?)",
            (_t(), key, request_hash, recon_id, _now()),
        )


def prune_idempotency(older_than_seconds: int = 24 * 3600) -> int:
    """Drop entries older than the TTL. Returns rows deleted."""
    cutoff = datetime.now(timezone.utc).timestamp() - older_than_seconds
    cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).isoformat(timespec="seconds")
    with conn() as c:
        cur = c.execute(
            "DELETE FROM idempotency_keys WHERE created_at < ?", (cutoff_iso,)
        )
        return cur.rowcount


def delete_live_fixture(fixture_id: str):
    with conn() as c:
        c.execute(
            "DELETE FROM live_fixtures WHERE tenant_id = ? AND fixture_id = ?",
            (_t(), fixture_id),
        )


# ---------- maintenance ----------

def reset_all():
    """Drop and re-init — for tests."""
    global _initialized
    if DB_PATH.exists():
        DB_PATH.unlink()
    _initialized = False
    init_db()
