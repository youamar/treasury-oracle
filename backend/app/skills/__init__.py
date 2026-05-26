"""Skills package — every capability of the platform.

Importing this package side-effects the registration of every skill.
"""
from ._base import (
    SkillDef, SkillContext, SKILL_REGISTRY,
    register, get_skill, all_skills,
    enabled_tool_skills, resolve_skill_config,
)

# Side-effect imports register each skill into SKILL_REGISTRY.
from . import fx_lookup        # noqa: F401
from . import bank_fee         # noqa: F401
from . import fuzzy_compare    # noqa: F401
from . import swift_route      # noqa: F401
from . import memory           # noqa: F401
from . import capabilities     # noqa: F401

__all__ = [
    "SkillDef", "SkillContext", "SKILL_REGISTRY",
    "register", "get_skill", "all_skills",
    "enabled_tool_skills", "resolve_skill_config",
]
