"""Project-owned model config (0.10.30): a consuming project patches the plugin's tier
DEFINITIONS — provider, model, effort, budget — adds tiers, extends or redefines providers,
and replaces `default_tier` and `ladder`, in its own harness.yaml.

Two questions kept apart: `agent_tiers:` answers which tier an agent runs on (selection);
`tiers:` here answers what a tier is (definition). Maps patch, scalars and lists replace,
and the MERGED config is what gets validated — validating before the merge would judge a
config nobody runs.
"""

from __future__ import annotations

import pytest

from models import resolve as mod
from models.project import Project, ProjectError

PLUGIN = {
    "default_tier": "strong",
    "ladder": ["worker", "strong", "strategic"],
    "tiers": {
        "worker": {"provider": "anthropic", "model": "claude-sonnet-5", "effort": "high", "max_budget_usd": 3.0},
        "strong": {"provider": "anthropic", "model": "claude-opus-5[1m]", "effort": "xhigh", "max_budget_usd": 4.0},
        "strategic": {"provider": "anthropic", "model": "claude-opus-5[1m]", "effort": "max", "max_budget_usd": 8.0},
    },
    "providers": {
        "anthropic": {"env": {}},
        "deepseek": {"env": {"ANTHROPIC_BASE_URL": "${DEEPSEEK_BASE_URL}", "ANTHROPIC_AUTH_TOKEN": "${DEEPSEEK_API_KEY}"}},
    },
}


def _project(raw):
    return Project(name="x", slug="x", stacks=(), paths={}, areas=(), security={}, raw=raw)


@pytest.fixture
def agents(tmp_path):
    (tmp_path / "lens.md").write_text("---\nname: lens\nmodel_tier: strong\n---\nbody\n")
    (tmp_path / "untiered.md").write_text("---\nname: untiered\n---\nbody\n")
    (tmp_path / "grunt.md").write_text("---\nname: grunt\nmodel_tier: worker\n---\nbody\n")
    return tmp_path


def merged(raw):
    return mod._validate(mod.merge_model_config(PLUGIN, _project(raw).model_config()), where="test")


# --- the merge -----------------------------------------------------------------------------


def test_a_project_redefining_a_tiers_provider_and_model_changes_what_resolve_returns(agents):
    cfg = merged({"providers": {"openrouter": {"env": {"ANTHROPIC_BASE_URL": "https://openrouter.ai/api", "ANTHROPIC_AUTH_TOKEN": "${OPENROUTER_API_KEY}"}}},
                  "tiers": {"worker": {"provider": "openrouter", "model": "qwen/qwen3-coder-plus"}}})
    r = mod.resolve("grunt", config=cfg, agents_dir=agents, project_tiers={})
    assert (r.provider, r.model) == ("openrouter", "qwen/qwen3-coder-plus")
    assert r.effort == "high" and r.max_budget_usd == 3.0, "the keys the project did not name are the plugin's"


def test_patching_only_the_budget_inherits_provider_model_and_effort(agents):
    cfg = merged({"tiers": {"worker": {"max_budget_usd": 9.5}}})
    assert cfg["tiers"]["worker"] == {"provider": "anthropic", "model": "claude-sonnet-5", "effort": "high", "max_budget_usd": 9.5}
    r = mod.resolve("grunt", config=cfg, agents_dir=agents, project_tiers={})
    assert r.max_budget_usd == 9.5 and r.model == "claude-sonnet-5"


def test_a_brand_new_tier_is_selectable_through_agent_tiers_and_keeps_declaration_order(agents):
    raw = {"tiers": {"candidate": {"provider": "deepseek", "model": "deepseek-v4", "effort": "high", "max_budget_usd": 1.0}},
           "ladder": ["worker", "candidate", "strong", "strategic"]}
    cfg = merged(raw)
    assert list(cfg["tiers"]) == ["worker", "strong", "strategic", "candidate"], "new tiers append; redefined ones keep their place"
    over = _project({**raw, "agent_tiers": {"lens": "candidate"}}).agent_tiers(config=cfg, agents_dir=agents)
    r = mod.resolve("lens", config=cfg, agents_dir=agents, project_tiers=over)
    assert r.tier == "candidate" and r.provider == "deepseek" and r.reason == mod.PROJECT_OVERRIDE


def test_a_redefined_tier_keeps_its_position(agents):
    cfg = merged({"tiers": {"strong": {"model": "claude-opus-5"}}})
    assert list(cfg["tiers"]) == ["worker", "strong", "strategic"]
    assert cfg["tiers"]["strong"]["model"] == "claude-opus-5" and cfg["tiers"]["strong"]["effort"] == "xhigh"


def test_a_project_default_tier_moves_an_undeclared_agent_and_the_reason_still_reads_global_default(agents):
    cfg = merged({"default_tier": "worker"})
    r = mod.resolve("untiered", config=cfg, agents_dir=agents, project_tiers={})
    assert r.tier == "worker" and r.reason == "global default"


def test_a_project_ladder_replaces_the_plugins_outright_and_escalation_walks_it(agents):
    from models.escalate import next_tier

    raw = {"tiers": {"candidate": {"provider": "deepseek", "model": "deepseek-v4", "effort": "high", "max_budget_usd": 1.0}},
           "ladder": ["candidate", "worker", "strong", "strategic"]}
    cfg = merged(raw)
    assert cfg["ladder"] == ["candidate", "worker", "strong", "strategic"], "replaced, never interleaved"
    assert next_tier("candidate", cfg) == "worker" and next_tier("strong", cfg) == "strategic"


def test_overriding_anthropics_env_merges_per_key_not_the_whole_map():
    plugin = {**PLUGIN, "providers": {**PLUGIN["providers"], "anthropic": {"env": {"ANTHROPIC_BASE_URL": "https://proxy.example", "KEEP": "1"}}}}
    cfg = mod.merge_model_config(plugin, _project({"providers": {"anthropic": {"env": {"ANTHROPIC_BASE_URL": "https://other.example"}}}}).model_config())
    assert cfg["providers"]["anthropic"]["env"] == {"ANTHROPIC_BASE_URL": "https://other.example", "KEEP": "1"}
    assert "deepseek" in cfg["providers"], "other providers survive"


def test_merge_project_false_is_the_plugins_shipped_config_whatever_the_project_says(monkeypatch):
    monkeypatch.setattr(mod, "_project_model_config", lambda: {"tiers": {"worker": {"model": "qwen/x", "provider": "anthropic"}}})
    shipped = mod.load_config(merge_project=False)
    assert shipped["tiers"]["worker"]["model"] == "claude-sonnet-5"
    live = mod.load_config()
    assert live["tiers"]["worker"]["model"] == "qwen/x"


def test_no_project_config_at_all_is_todays_behaviour(monkeypatch):
    monkeypatch.setattr(mod, "_project_model_config", lambda: {})
    assert mod.load_config()["tiers"]["worker"]["model"] == "claude-sonnet-5"
    assert _project({}).model_config() == {}


# --- shape validation in Project.model_config ----------------------------------------------


@pytest.mark.parametrize(
    "raw, msg",
    [
        ({"tiers": ["worker"]}, "tiers: must be a map"),
        ({"tiers": {"worker": "fast"}}, "tiers.worker: must be a map"),
        ({"tiers": {"worker": {"modle": "x"}}}, "tiers.worker: unknown key(s) modle"),
        ({"providers": {"openrouter": {"url": "x"}}}, "providers.openrouter: unknown key(s) url"),
        ({"providers": {"openrouter": {"env": "x"}}}, "providers.openrouter.env: must be a map"),
        ({"default_tier": 3}, "default_tier: must be a tier name"),
        ({"ladder": "worker,strong"}, "ladder: must be a list"),
    ],
)
def test_a_malformed_block_is_refused_by_name(raw, msg):
    import re

    with pytest.raises(ProjectError, match=re.escape(msg)):
        _project(raw).model_config()


def test_the_merged_config_is_what_is_validated():
    """A tier the project adds with a provider nobody defined fails the merged check,
    naming the tier and the provider — before the merge it would have looked fine."""
    with pytest.raises(mod.ConfigError, match="tier 'candidate' names provider 'nowhere'"):
        merged({"tiers": {"candidate": {"provider": "nowhere", "model": "m", "effort": "high", "max_budget_usd": 1.0}}})
    with pytest.raises(mod.ConfigError, match="default_tier 'ghost'"):
        merged({"default_tier": "ghost"})
