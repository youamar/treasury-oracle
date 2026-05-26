"""Platform configuration — customer-editable settings for the skill platform.

Persisted in SQLite (`platform_config` table, single-row keyed by `id='current'`).
Shape:
    {
        "enabled_skills": ["get_fx_rate", "apply_bank_fee", ...] | None  (None = all defaults),
        "skill_overrides": {
            "<skill_id>": {"system_prompt": "...", "<any_field>": ...}
        },
        "model_profile": "default",
        "business_profile": "free-form text the wizard used to seed the config",
        "updated_at": "..."
    }
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .db import init_db
from . import db as _db


def _db_path():
    # Indirected so tests that monkeypatch db.DB_PATH are honored.
    return _db.DB_PATH


_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS platform_config (
    tenant_id TEXT NOT NULL,
    id TEXT NOT NULL,
    config_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, id)
);
"""

# Pre-tenant deployments had a single global row keyed on id='current'.
# Migration: add the tenant_id column with default 'default'.
_MIGRATE_SQL = """
ALTER TABLE platform_config ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
"""


def _ensure_table():
    init_db()
    with sqlite3.connect(_db_path()) as c:
        c.executescript(_TABLE_SQL)
        # Best-effort migration for an older table that lacked tenant_id.
        cols = [r[1] for r in c.execute("PRAGMA table_info(platform_config)").fetchall()]
        if "tenant_id" not in cols:
            try:
                c.executescript(_MIGRATE_SQL)
            except sqlite3.OperationalError:
                pass
        c.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_config() -> dict:
    from .skills import all_skills
    return {
        "enabled_skills": [s.id for s in all_skills() if s.default_enabled],
        "skill_overrides": {},
        "model_profile": "default",
        "business_profile": "",
        "updated_at": _now(),
    }


def load_config() -> dict:
    _ensure_table()
    t = _db.current_tenant()
    with sqlite3.connect(_db_path()) as c:
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT config_json FROM platform_config "
            "WHERE tenant_id = ? AND id = 'current'",
            (t,),
        ).fetchone()
    if row is None:
        cfg = default_config()
        save_config(cfg)
        return cfg
    return json.loads(row["config_json"])


def _hash_prompt(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _snapshot_prompt_versions(cfg: dict, edited_by: str = "user") -> None:
    """For each overridden system_prompt, persist a row in prompt_versions if new."""
    overrides = cfg.get("skill_overrides") or {}
    for skill_id, ov in overrides.items():
        prompt = (ov or {}).get("system_prompt")
        if not prompt:
            continue
        _db.record_prompt_version(
            skill_id=skill_id, prompt_text=prompt,
            version_hash=_hash_prompt(prompt), edited_by=edited_by,
        )


def save_config(cfg: dict, edited_by: str = "user") -> dict:
    _ensure_table()
    cfg = {**cfg, "updated_at": _now()}
    t = _db.current_tenant()
    with sqlite3.connect(_db_path()) as c:
        c.execute(
            "INSERT OR REPLACE INTO platform_config"
            "(tenant_id, id, config_json, updated_at) VALUES (?, 'current', ?, ?)",
            (t, json.dumps(cfg), cfg["updated_at"]),
        )
        c.commit()
    try:
        _snapshot_prompt_versions(cfg, edited_by=edited_by)
    except Exception:
        # Versioning is observational — never break a config save.
        pass
    return cfg


def active_prompt_versions(cfg: dict | None = None) -> dict[str, str]:
    """Map skill_id -> 16-char hash of the currently effective system prompt
    (override if present, else default). Used to tag sessions and eval runs."""
    from .skills import all_skills, resolve_skill_config
    if cfg is None:
        cfg = load_config()
    out: dict[str, str] = {}
    for s in all_skills():
        sc = resolve_skill_config(s, cfg)
        out[s.id] = _hash_prompt(sc.get("system_prompt") or s.default_system_prompt)
    return out


def update_skill_override(skill_id: str, override: dict) -> dict:
    cfg = load_config()
    overrides = dict(cfg.get("skill_overrides") or {})
    existing = dict(overrides.get(skill_id) or {})
    existing.update(override)
    overrides[skill_id] = existing
    cfg["skill_overrides"] = overrides
    return save_config(cfg)


def set_enabled_skills(skill_ids: list[str]) -> dict:
    cfg = load_config()
    cfg["enabled_skills"] = list(skill_ids)
    return save_config(cfg)


def reset_to_defaults() -> dict:
    return save_config(default_config())
