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


# ---------- per-tenant agent knobs (C-1, C-3, C-4) ----------

class AgentKnobs(BaseModel):
    model_config = {"protected_namespaces": ()}
    max_steps: int | None = None
    date_window_days: int | None = None
    reflection_confidence_threshold: float | None = None
    reflection_max_cycles: int | None = None
    verifier_strict_diff_pct: float | None = None
    verifier_strict_days_off: int | None = None
    verifier_min_tool_calls_for_strict: int | None = None
    match_tolerance: float | None = None
    agent_temperature: float | None = None
    verifier_llm_enabled: bool | None = None
    verifier_model_profile: str | None = None
    base_prompt: str | None = None


@router.get("/agent-knobs")
def get_agent_knobs():
    from .agent import AGENT_KNOBS_DEFAULTS, _BASE_PROMPT
    cfg = platform_config.load_config()
    current = (cfg.get("agent_knobs") or {})
    return {
        "defaults": AGENT_KNOBS_DEFAULTS,
        "current": current,
        "default_base_prompt": _BASE_PROMPT,
    }


@router.put("/agent-knobs")
def put_agent_knobs(body: AgentKnobs):
    """Per-tenant overrides for agent step budget, reflection thresholds,
    verifier thresholds, match tolerance, temperature, and the base agent
    prompt. Any field omitted (or null) keeps the existing override (or
    falls through to the built-in default if no override is set)."""
    cfg = platform_config.load_config()
    knobs = dict(cfg.get("agent_knobs") or {})
    for k, v in body.model_dump(exclude_none=True).items():
        knobs[k] = v
    cfg["agent_knobs"] = knobs
    return platform_config.save_config(cfg)


@router.post("/agent-knobs/reset")
def reset_agent_knobs():
    cfg = platform_config.load_config()
    cfg["agent_knobs"] = {}
    return platform_config.save_config(cfg)


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
    # When apply=true and the caller already has a proposed config from a
    # prior apply=false round-trip, pass it here to skip the LLM call and
    # save directly. Avoids a second 30s Chutes round-trip on Accept.
    proposed: dict | None = None


_WIZARD_SYSTEM = """You configure a treasury-reconciliation skill platform.
You will be given a description of the customer's business, a catalog of
available skills (id, name, kind, default purpose), and the current default
system_prompt for each.

OUTPUT FORMAT — read this carefully:
- Your ENTIRE response is a single JSON object and nothing else.
- The first character of your response MUST be `{`.
- Do NOT write "Thinking Process:", "Here is the config:", or any preamble.
- Do NOT wrap the JSON in ```json ... ``` code fences.
- Do NOT add any commentary before or after the JSON.

JSON shape:
{
  "enabled_skills": ["<skill_id>", ...],
  "skill_overrides": {
    "<skill_id>": {"system_prompt": "<tuned prompt that respects the customer's profile>"}
  },
  "rationale": "<2-3 sentence summary of choices>"
}

Rules for the content of the JSON:
- Only enable skills that genuinely fit the customer's needs. It is fine to
  disable capabilities the customer will not use.
- Only override a system_prompt when the customer's profile demands a real
  change in tone, language, currency defaults, or strictness. Otherwise omit.
- Keep enabled_skills as a sorted list of skill ids you want active.
"""


@router.post("/wizard")
def wizard(body: WizardRequest):
    cur = platform_config.load_config()

    # Fast path: caller passed a pre-proposed config and just wants to save.
    if body.apply and body.proposed:
        valid_ids = {s.id for s in all_skills()}
        proposed = dict(body.proposed)
        proposed["enabled_skills"] = sorted(
            sid for sid in (proposed.get("enabled_skills") or []) if sid in valid_ids
        )
        proposed["skill_overrides"] = {
            sid: ov for sid, ov in (proposed.get("skill_overrides") or {}).items()
            if sid in valid_ids
        }
        new_cfg = {
            **cur,
            "enabled_skills": proposed["enabled_skills"],
            "skill_overrides": proposed["skill_overrides"],
            "business_profile": body.business_profile,
        }
        saved = platform_config.save_config(new_cfg)
        return {"applied": True, "config": saved, "rationale": proposed.get("rationale", "")}

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

    # Route through chutes_client.chat (not the raw OpenAI client) so the
    # call gets multi-provider failover, breaker integration, and the
    # ONE_SHOT_POLICY (single attempt, no 3x retry on a slow reasoning
    # model). max_tokens raised because the wizard's structured-config
    # output is large + reasoning models burn 500-1000 tokens thinking;
    # at 1500 the JSON was being truncated mid-output -> parse error.
    from .chutes_client import chat as llm_chat
    from .reliability import ONE_SHOT_POLICY
    try:
        resp = llm_chat(
            messages=[
                {"role": "system", "content": _WIZARD_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            model=REASONING_MODEL,
            temperature=0.2,
            max_tokens=4000,
            timeout=90,
            response_format={"type": "json_object"},
            retry_policy=ONE_SHOT_POLICY,
        )
    except Exception as e:
        raise HTTPException(502, f"wizard LLM call failed: {e}")

    from .chutes_client import extract_content, strip_code_fences, _last_json_block
    raw = strip_code_fences(extract_content(resp))
    try:
        proposed = json.loads(raw)
    except json.JSONDecodeError:
        # Reasoning models sometimes ignore the "JSON only" instruction and
        # emit a preamble ("Thinking Process: ... Here is the config: {...}").
        # Salvage by extracting the LAST balanced {...} block, which is
        # where the actual answer lands when the model thinks out loud.
        salvaged = _last_json_block(raw)
        if salvaged:
            try:
                proposed = json.loads(salvaged)
            except json.JSONDecodeError as e:
                raise HTTPException(502,
                    f"wizard returned invalid JSON even after salvage: {e}; raw={raw[:300]}")
        else:
            raise HTTPException(502,
                f"wizard returned no JSON object; raw={raw[:300]}")

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
