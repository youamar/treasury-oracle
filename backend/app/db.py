"""SQLite persistence layer.

Single-file DB at backend/data/treasury.db. All state that used to live in
module-globals (sessions, campaigns, watchers, aliases, agent traces) is
persisted here. Thread-safe via per-call connections + WAL mode.
"""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "treasury.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
_init_lock = threading.Lock()
_initialized = False


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    bank TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    trace_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

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
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_matches_session ON matches(session_id);

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
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_disc_session ON discrepancies(session_id);

CREATE TABLE IF NOT EXISTS unmatched_txns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    txn_json TEXT NOT NULL,
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
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watchers (
    id TEXT PRIMARY KEY,
    from_ccy TEXT NOT NULL,
    to_ccy TEXT NOT NULL,
    target_rate REAL NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payer_aliases (
    canonical TEXT PRIMARY KEY,
    observed TEXT NOT NULL,
    learned_at TEXT NOT NULL
);

-- agent_trace deliberately has no FK on sessions: we append trace rows
-- *during* the agent loop, before the session row exists.
CREATE TABLE IF NOT EXISTS agent_trace (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    proof_source TEXT,
    step INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trace_session ON agent_trace(session_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db(path: Path | None = None):
    """Idempotent schema creation. Safe to call on every startup."""
    global _initialized
    with _init_lock:
        p = path or DB_PATH
        with sqlite3.connect(p) as c:
            c.executescript("PRAGMA journal_mode=WAL;")
            c.executescript(SCHEMA)
            c.commit()
        _initialized = True


@contextmanager
def conn():
    if not _initialized:
        init_db()
    c = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    try:
        yield c
    finally:
        c.close()


# ---------- sessions ----------

def save_session(session_id: str, bank: str, result: dict):
    with conn() as c:
        c.execute("BEGIN")
        c.execute(
            "INSERT OR REPLACE INTO sessions(id, bank, summary_json, trace_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, bank, json.dumps(result["summary"]),
             json.dumps(result.get("trace", [])), _now()),
        )
        # Replace child tables
        for tbl in ("matches", "soft_matches", "discrepancies", "unmatched_txns"):
            c.execute(f"DELETE FROM {tbl} WHERE session_id = ?", (session_id,))

        for i, m in enumerate(result.get("matches", [])):
            c.execute(
                "INSERT INTO matches(session_id, match_index, proof_json, txn_json, "
                "conversion_json, confidence, reasoning, status) VALUES (?,?,?,?,?,?,?,?)",
                (session_id, i, json.dumps(m["proof"]), json.dumps(m["txn"]),
                 json.dumps(m["conversion"]), m["confidence"], m["reasoning"], m["status"]),
            )
        for s in result.get("soft_matches", []):
            c.execute(
                "INSERT INTO soft_matches(session_id, proof_json, txn_json, conversion_json, "
                "confidence, signals_json, reasoning) VALUES (?,?,?,?,?,?,?)",
                (session_id, json.dumps(s["proof"]), json.dumps(s["txn"]),
                 json.dumps(s["conversion"]), s["confidence"],
                 json.dumps(s.get("signals", [])), s["reasoning"]),
            )
        for d in result.get("unmatched_proofs", []):
            c.execute(
                "INSERT INTO discrepancies(session_id, proof_json, reason, closest_txn_json, "
                "swift_route_json) VALUES (?,?,?,?,?)",
                (session_id, json.dumps(d), d.get("reason", ""),
                 json.dumps(d.get("closest_txn")) if d.get("closest_txn") else None,
                 json.dumps(d.get("swift_route")) if d.get("swift_route") else None),
            )
        for t in result.get("unmatched_txns", []):
            c.execute(
                "INSERT INTO unmatched_txns(session_id, txn_json) VALUES (?, ?)",
                (session_id, json.dumps(t)),
            )
        c.execute("COMMIT")


def load_session(session_id: str) -> dict | None:
    with conn() as c:
        s = c.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not s:
            return None
        matches = [_match_row(r) for r in c.execute(
            "SELECT * FROM matches WHERE session_id = ? ORDER BY match_index", (session_id,))]
        soft = [_soft_row(r) for r in c.execute(
            "SELECT * FROM soft_matches WHERE session_id = ?", (session_id,))]
        disc = [_disc_row(r) for r in c.execute(
            "SELECT * FROM discrepancies WHERE session_id = ?", (session_id,))]
        unmatched_txns = [json.loads(r["txn_json"]) for r in c.execute(
            "SELECT * FROM unmatched_txns WHERE session_id = ?", (session_id,))]
        trace = [json.loads(r["payload_json"]) for r in c.execute(
            "SELECT * FROM agent_trace WHERE session_id = ? ORDER BY step", (session_id,))]
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
    """Mark a soft match as confirmed and copy it into matches."""
    with conn() as c:
        c.execute("BEGIN")
        s = c.execute("SELECT * FROM soft_matches WHERE id = ?", (soft_match_id,)).fetchone()
        if not s:
            c.execute("ROLLBACK")
            return None
        c.execute("UPDATE soft_matches SET confirmed = 1 WHERE id = ?", (soft_match_id,))
        # next match index
        idx_row = c.execute(
            "SELECT COALESCE(MAX(match_index), -1) + 1 AS i FROM matches WHERE session_id = ?",
            (s["session_id"],),
        ).fetchone()
        next_idx = idx_row["i"]
        c.execute(
            "INSERT INTO matches(session_id, match_index, proof_json, txn_json, "
            "conversion_json, confidence, reasoning, status) VALUES (?,?,?,?,?,?,?,?)",
            (s["session_id"], next_idx, s["proof_json"], s["txn_json"],
             s["conversion_json"], s["confidence"], s["reasoning"], "matched_via_soft"),
        )
        c.execute("COMMIT")
        return load_session(s["session_id"])


# ---------- agent trace ----------

def append_trace(session_id: str, proof_source: str | None, step: int,
                 type_: str, payload: dict):
    with conn() as c:
        c.execute(
            "INSERT INTO agent_trace(session_id, proof_source, step, type, "
            "payload_json, created_at) VALUES (?,?,?,?,?,?)",
            (session_id, proof_source, step, type_, json.dumps(payload), _now()),
        )


def get_trace(session_id: str) -> list[dict]:
    with conn() as c:
        return [
            {"step": r["step"], "type": r["type"], "proof_source": r["proof_source"],
             "payload": json.loads(r["payload_json"]), "at": r["created_at"]}
            for r in c.execute(
                "SELECT * FROM agent_trace WHERE session_id = ? ORDER BY id", (session_id,)
            )
        ]


# ---------- payer aliases ----------

def remember_alias(canonical: str, observed: str):
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO payer_aliases(canonical, observed, learned_at) "
            "VALUES (?, ?, ?)", (canonical, observed, _now()),
        )


def lookup_alias(canonical: str) -> str | None:
    with conn() as c:
        r = c.execute("SELECT observed FROM payer_aliases WHERE canonical = ?",
                      (canonical,)).fetchone()
        return r["observed"] if r else None


def all_aliases() -> dict[str, str]:
    with conn() as c:
        return {r["canonical"]: r["observed"]
                for r in c.execute("SELECT canonical, observed FROM payer_aliases")}


# ---------- campaigns ----------

def upsert_campaign(c_dict: dict):
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO campaigns(id, client_name, invoice_ref, invoice_amount, "
            "invoice_ccy, outstanding, due_date, current_stage, status, history_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (c_dict["id"], c_dict["client_name"], c_dict["invoice_ref"],
             c_dict["invoice_amount"], c_dict["invoice_ccy"], c_dict["outstanding"],
             c_dict["due_date"], c_dict["current_stage"], c_dict["status"],
             json.dumps(c_dict["history"]), c_dict.get("created_at", _now())),
        )


def get_campaign(cid: str) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM campaigns WHERE id = ?", (cid,)).fetchone()
        return _campaign_row(r) if r else None


def list_campaigns_db() -> list[dict]:
    with conn() as c:
        return [_campaign_row(r) for r in
                c.execute("SELECT * FROM campaigns ORDER BY created_at DESC")]


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
            "INSERT OR REPLACE INTO watchers(id, from_ccy, to_ccy, target_rate, note, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (w["id"], w["from_ccy"], w["to_ccy"], w["target_rate"],
             w.get("note", ""), w.get("created_at", _now())),
        )


def list_watchers() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM watchers ORDER BY created_at DESC")]


def delete_watcher(wid: str):
    with conn() as c:
        c.execute("DELETE FROM watchers WHERE id = ?", (wid,))


# ---------- maintenance ----------

def reset_all():
    """Drop and re-init — for tests."""
    global _initialized
    if DB_PATH.exists():
        DB_PATH.unlink()
    _initialized = False
    init_db()
