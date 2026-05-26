"""Tests for the skill registry, platform config, and platform API."""
import json
from fastapi.testclient import TestClient

from app.main import app
from app import platform_config
from app.skills import all_skills, SKILL_REGISTRY, enabled_tool_skills


client = TestClient(app)


def test_registry_loaded():
    ids = {s.id for s in all_skills()}
    # tool skills
    assert {"get_fx_rate", "apply_bank_fee", "fuzzy_compare", "trace_swift_route"} <= ids
    # capability skills
    assert {"dunning_email", "audit_defense_pack", "reconciliation_report"} <= ids


def test_default_config_enables_defaults():
    cfg = platform_config.load_config()
    assert isinstance(cfg["enabled_skills"], list)
    assert "get_fx_rate" in cfg["enabled_skills"]


def test_enabled_tool_skills_respects_config():
    cfg = {"enabled_skills": ["get_fx_rate", "apply_bank_fee"]}
    tools = enabled_tool_skills(cfg)
    ids = {s.id for s in tools}
    assert ids == {"get_fx_rate", "apply_bank_fee"}


def test_list_skills_endpoint():
    r = client.get("/api/platform/skills")
    assert r.status_code == 200
    data = r.json()
    assert "skills" in data and "config" in data
    fx = next(s for s in data["skills"] if s["id"] == "get_fx_rate")
    assert fx["enabled"] is True
    assert fx["kind"] == "tool"
    assert fx["system_prompt"] == fx["default_system_prompt"]


def test_update_skill_disable_and_override():
    r = client.put("/api/platform/skills/dunning_email",
                   json={"enabled": False, "system_prompt": "Be terse, no greeting."})
    assert r.status_code == 200
    cfg = r.json()
    assert "dunning_email" not in (cfg.get("enabled_skills") or [])
    assert cfg["skill_overrides"]["dunning_email"]["system_prompt"] == "Be terse, no greeting."


def test_update_skill_revert_to_default_prompt_clears_override():
    skill = SKILL_REGISTRY["dunning_email"]
    # set override
    client.put("/api/platform/skills/dunning_email",
               json={"system_prompt": "custom"})
    # revert
    r = client.put("/api/platform/skills/dunning_email",
                   json={"system_prompt": skill.default_system_prompt})
    cfg = r.json()
    assert "dunning_email" not in (cfg.get("skill_overrides") or {})


def test_reset_config():
    client.put("/api/platform/skills/dunning_email",
               json={"enabled": False, "system_prompt": "x"})
    r = client.post("/api/platform/config/reset")
    assert r.status_code == 200
    cfg = r.json()
    assert "dunning_email" in cfg["enabled_skills"]
    assert cfg["skill_overrides"] == {}


def test_unknown_skill_404():
    r = client.put("/api/platform/skills/does_not_exist",
                   json={"enabled": True})
    assert r.status_code == 404


def test_agent_runs_with_config_override(sample_proof, sample_txn):
    """Engine should respect config_override and not blow up with custom skill set."""
    from app.agent import reconcile_agent
    cfg = {"enabled_skills": ["get_fx_rate", "apply_bank_fee",
                              "fuzzy_compare", "trace_swift_route"],
           "skill_overrides": {}}
    result = reconcile_agent([sample_proof], [sample_txn], "default",
                             config_override=cfg)
    assert result["mode"] == "agent"
    assert "active_skills" in result
    assert set(result["active_skills"]) == set(cfg["enabled_skills"])


def test_agent_with_no_tool_skills_returns_clean_error(sample_proof, sample_txn):
    from app.agent import reconcile_agent
    result = reconcile_agent([sample_proof], [sample_txn], "default",
                             config_override={"enabled_skills": []})
    assert result.get("error") == "no_tool_skills_enabled"


def test_platform_config_is_per_tenant():
    """Regression: before D6, platform_config was a single global row keyed on
    id='current', so every tenant clobbered the same config."""
    from app import db as dbmod
    with dbmod.tenant_scope("acme"):
        platform_config.save_config({
            "enabled_skills": ["get_fx_rate"],
            "skill_overrides": {},
            "model_profile": "default",
            "business_profile": "acme inc",
        })
    with dbmod.tenant_scope("globex"):
        platform_config.save_config({
            "enabled_skills": ["fuzzy_compare"],
            "skill_overrides": {},
            "model_profile": "cheap",
            "business_profile": "globex llc",
        })
    with dbmod.tenant_scope("acme"):
        a = platform_config.load_config()
    with dbmod.tenant_scope("globex"):
        g = platform_config.load_config()
    assert a["enabled_skills"] == ["get_fx_rate"]
    assert g["enabled_skills"] == ["fuzzy_compare"]
    assert a["business_profile"] == "acme inc"
    assert g["business_profile"] == "globex llc"
