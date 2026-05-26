"""Platform API — configure the skill platform manually or via the AI wizard."""
from __future__ import annotations

import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import platform_config
from .skills import all_skills, get_skill, resolve_skill_config
from .chutes_client import get_client
from .config import REASONING_MODEL, MODEL_PROFILES


router = APIRouter(prefix="/api/platform", tags=["platform"])


# ---------- Skill catalog ----------

@router.get("/skills")
def list_skills():
    cfg = platform_config.load_config()
    enabled = set(cfg.get("enabled_skills") or [])
    out = []
    for s in all_skills():
        resolved = resolve_skill_config(s, cfg)
        out.append({
            "id": s.id,
            "name": s.name,
            "kind": s.kind,
            "category": s.category,
            "tags": list(s.tags),
            "description": s.description,
            "default_enabled": s.default_enabled,
            "enabled": s.id in enabled,
            "system_prompt": resolved.get("system_prompt", s.default_system_prompt),
            "default_system_prompt": s.default_system_prompt,
            "model_profile": resolved.get("model_profile", s.model_profile),
            "default_model_profile": s.model_profile,
            "is_overridden": (cfg.get("skill_overrides") or {}).get(s.id) is not None,
        })
    return {"skills": out, "config": cfg}


# ---------- Config CRUD ----------

@router.get("/config")
def get_config():
    return platform_config.load_config()


@router.get("/model-profiles")
def get_model_profiles():
    return {"profiles": MODEL_PROFILES}


class FullConfig(BaseModel):
    model_config = {"protected_namespaces": ()}
    enabled_skills: list[str] | None = None
    skill_overrides: dict | None = None
    model_profile: str | None = None
    business_profile: str | None = None


@router.put("/config")
def put_config(body: FullConfig):
    cur = platform_config.load_config()
    new = {**cur, **{k: v for k, v in body.model_dump().items() if v is not None}}
    return platform_config.save_config(new)


@router.post("/config/reset")
def reset_config():
    return platform_config.reset_to_defaults()


# ---------- Per-skill update ----------

class SkillUpdate(BaseModel):
    model_config = {"protected_namespaces": ()}
    enabled: bool | None = None
    system_prompt: str | None = None
    model_profile: str | None = None


@router.put("/skills/{skill_id}")
def update_skill(skill_id: str, body: SkillUpdate):
    skill = get_skill(skill_id)
    if skill is None:
        raise HTTPException(404, f"unknown skill {skill_id}")
    cfg = platform_config.load_config()

    if body.enabled is not None:
        enabled = set(cfg.get("enabled_skills") or [])
        if body.enabled:
            enabled.add(skill_id)
        else:
            enabled.discard(skill_id)
        cfg["enabled_skills"] = sorted(enabled)

    overrides = dict(cfg.get("skill_overrides") or {})
    existing = dict(overrides.get(skill_id) or {})

    if body.system_prompt is not None:
        if body.system_prompt.strip() == skill.default_system_prompt.strip():
            existing.pop("system_prompt", None)
        else:
            existing["system_prompt"] = body.system_prompt

    if body.model_profile is not None:
        if body.model_profile == skill.model_profile:
            existing.pop("model_profile", None)
        else:
            existing["model_profile"] = body.model_profile

    if existing:
        overrides[skill_id] = existing
    else:
        overrides.pop(skill_id, None)
    cfg["skill_overrides"] = overrides

    return platform_config.save_config(cfg)


# ---------- AI-assisted wizard ----------

class WizardRequest(BaseModel):
    business_profile: str
    apply: bool = True  # if false, return proposed config without saving


_WIZARD_SYSTEM = """You configure a treasury-reconciliation skill platform.
You will be given a description of the customer's business, a catalog of
available skills (id, name, kind, default purpose), and the current default
system_prompt for each.

Return STRICT JSON only (no commentary, no code fences) in this shape:

{
  "enabled_skills": ["<skill_id>", ...],
  "skill_overrides": {
    "<skill_id>": {"system_prompt": "<tuned prompt that respects the customer's profile>"}
  },
  "rationale": "<2-3 sentence summary of choices>"
}

Rules:
- Only enable skills that genuinely fit the customer's needs. It is fine to
  disable capabilities the customer will not use.
- Only override a system_prompt when the customer's profile demands a real
  change in tone, language, currency defaults, or strictness. Otherwise omit.
- Keep enabled_skills as a sorted list of skill ids you want active.
"""


@router.post("/wizard")
def wizard(body: WizardRequest):
    cur = platform_config.load_config()
    catalog = []
    for s in all_skills():
        catalog.append({
            "id": s.id, "name": s.name, "kind": s.kind,
            "category": s.category, "description": s.description,
            "default_system_prompt": s.default_system_prompt,
        })
    user_msg = (
        f"BUSINESS PROFILE:\n{body.business_profile}\n\n"
        f"SKILL CATALOG:\n{json.dumps(catalog, ensure_ascii=False, indent=2)}\n\n"
        f"CURRENT CONFIG:\n{json.dumps(cur, ensure_ascii=False, indent=2)}"
    )

    client = get_client(False)
    try:
        resp = client.chat.completions.create(
            model=REASONING_MODEL,
            messages=[
                {"role": "system", "content": _WIZARD_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.2,
            max_tokens=1500,
            timeout=45,
        )
    except Exception as e:
        raise HTTPException(502, f"wizard LLM call failed: {e}")

    raw = (resp.choices[0].message.content or "").strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        if len(parts) >= 2:
            inner = parts[1]
            if inner.startswith("json"):
                inner = inner[4:]
            raw = inner.strip()
    try:
        proposed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(502, f"wizard returned invalid JSON: {e}; raw={raw[:300]}")

    valid_ids = {s.id for s in all_skills()}
    proposed["enabled_skills"] = sorted(
        sid for sid in (proposed.get("enabled_skills") or []) if sid in valid_ids
    )
    proposed["skill_overrides"] = {
        sid: ov for sid, ov in (proposed.get("skill_overrides") or {}).items()
        if sid in valid_ids
    }

    if not body.apply:
        return {"proposed": proposed, "applied": False}

    new_cfg = {
        **cur,
        "enabled_skills": proposed["enabled_skills"],
        "skill_overrides": proposed["skill_overrides"],
        "business_profile": body.business_profile,
    }
    saved = platform_config.save_config(new_cfg)
    return {"applied": True, "config": saved, "rationale": proposed.get("rationale", "")}
