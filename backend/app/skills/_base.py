"""Skill framework — every capability is a Skill.

Two kinds:
  - "tool":       called inside the agent LLM tool-call loop. Has a JSON-schema
                  for parameters and a synchronous handler. Surfaced to the LLM
                  as an OpenAI-style tool spec.
  - "capability": called directly via an API endpoint (dunning, audit pack,
                  recon report, etc.). Has its own system_prompt or template
                  that the platform owner can edit.

Each skill ships defaults; the platform_config layer can override
`enabled` and `system_prompt` per-customer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional


SkillKind = Literal["tool", "capability"]


@dataclass
class SkillContext:
    """Threaded into every skill handler."""
    session_id: str
    config: dict                      # full resolved platform_config
    skill_config: dict                # this skill's resolved config (defaults + overrides)
    extras: dict = field(default_factory=dict)  # caller-supplied (proof, candidates, etc.)


@dataclass
class SkillDef:
    id: str
    name: str
    kind: SkillKind
    description: str                       # shown to LLM (tool) or in settings UI (capability)
    default_system_prompt: str             # editable per-customer
    handler: Callable[..., Any]            # tool: handler(ctx, **args); capability: handler(ctx, **kwargs)
    parameters: dict = field(default_factory=dict)  # JSON schema (tool-kind only)
    default_enabled: bool = True
    category: str = "general"
    tags: tuple[str, ...] = ()
    # Model routing — references a key in MODEL_PROFILES. Skills that don't
    # need a powerful model (e.g. fuzzy compare) point at 'cheap'; skills
    # like SWIFT inference can point at 'strong'.
    model_profile: str = "default"

    def to_openai_tool(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": self.parameters or {"type": "object", "properties": {}},
            },
        }


SKILL_REGISTRY: dict[str, SkillDef] = {}


def register(skill: SkillDef) -> SkillDef:
    if skill.id in SKILL_REGISTRY:
        raise ValueError(f"Skill {skill.id!r} already registered")
    SKILL_REGISTRY[skill.id] = skill
    return skill


def get_skill(skill_id: str) -> Optional[SkillDef]:
    return SKILL_REGISTRY.get(skill_id)


def all_skills() -> list[SkillDef]:
    return list(SKILL_REGISTRY.values())


def enabled_tool_skills(config: dict) -> list[SkillDef]:
    """Filter to tool-kind skills that are enabled in the platform config.

    `enabled_skills = None`  → use every default-enabled skill.
    `enabled_skills = []`    → explicitly enable nothing (caller meant it).
    """
    listed = config.get("enabled_skills")
    if listed is None:
        enabled = {s.id for s in all_skills() if s.default_enabled}
    else:
        enabled = set(listed)
    return [s for s in all_skills() if s.kind == "tool" and s.id in enabled]


def resolve_skill_config(skill: SkillDef, platform_config: dict) -> dict:
    """Merge skill defaults with per-skill overrides from platform_config."""
    overrides = (platform_config.get("skill_overrides") or {}).get(skill.id, {})
    return {
        "system_prompt": overrides.get("system_prompt", skill.default_system_prompt),
        "model_profile": overrides.get("model_profile", skill.model_profile),
        **overrides,
    }
