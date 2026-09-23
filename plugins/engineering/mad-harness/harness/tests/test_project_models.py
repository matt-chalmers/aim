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

#: A tier off Anthropic must declare its published rates — the CLI cannot price a
#: third-party endpoint (measured: $5.00/Mtok for DeepSeek). See models/pricing.py.
PRICE = {"input_per_mtok": 1.32, "output_per_mtok": 3.96, "cache_read_per_mtok": 0.044}

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
                  "tiers": {"worker": {"provider": "openrouter", "model": "qwen/qwen3-coder-plus", "price": PRICE}}})
    r = mod.resolve("grunt", config=cfg, agents_dir=agents, project_tiers={})
    assert (r.provider, r.model) == ("openrouter", "qwen/qwen3-coder-plus")
    assert r.effort == "high" and r.max_budget_usd == 3.0, "the keys the project did not name are the plugin's"


def test_patching_only_the_budget_inherits_provider_model_and_effort(agents):
    cfg = merged({"tiers": {"worker": {"max_budget_usd": 9.5}}})
    assert cfg["tiers"]["worker"] == {"provider": "anthropic", "model": "claude-sonnet-5", "effort": "high", "max_budget_usd": 9.5}
    r = mod.resolve("grunt", config=cfg, agents_dir=agents, project_tiers={})
    assert r.max_budget_usd == 9.5 and r.model == "claude-sonnet-5"


def test_a_brand_new_tier_is_selectable_through_agent_tiers_and_keeps_declaration_order(agents):
    raw = {"tiers": {"candidate": {"provider": "deepseek", "model": "deepseek-v4", "effort": "high", "max_budget_usd": 1.0, "price": PRICE}},
           "ladder": ["worker", "candidate", "strong", "strategic"]}
    cfg = merged(raw)
    assert list(cfg["tiers"]) == ["worker", "strong", "strategic", "candidate"], "new tiers append; redefined ones keep their place"
    over = _project({**raw, "agent_tiers": {"lens": "candidate"}}).agent_tiers(config=cfg, agents_dir=agents)
    r = mod.resolve("lens", config=cfg, agents_dir=agents, project_tiers=over)
    assert r.tier == "candidate" and r.provider == "deepseek" and r.reason == mod.PROJECT_OVERRIDE


def test_a_redefined_tier_keeps_its_position(agents):
    cfg = merged({"tiers": {"strong": {"provider": "anthropic", "model": "claude-opus-5"}}})
    assert list(cfg["tiers"]) == ["worker", "strong", "strategic"]
    assert cfg["tiers"]["strong"]["model"] == "claude-opus-5" and cfg["tiers"]["strong"]["effort"] == "xhigh"


def test_a_project_default_tier_moves_an_undeclared_agent_and_the_reason_still_reads_global_default(agents):
    cfg = merged({"default_tier": "worker"})
    r = mod.resolve("untiered", config=cfg, agents_dir=agents, project_tiers={})
    assert r.tier == "worker" and r.reason == "global default"


def test_a_project_ladder_replaces_the_plugins_outright_and_escalation_walks_it(agents):
    from models.escalate import next_tier

    raw = {"tiers": {"candidate": {"provider": "deepseek", "model": "deepseek-v4", "effort": "high", "max_budget_usd": 1.0, "price": PRICE}},
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
    monkeypatch.setattr(mod, "_project_model_config", lambda: {"tiers": {"worker": {"provider": "anthropic", "model": "qwen/x"}}})
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
        merged({"tiers": {"candidate": {"provider": "nowhere", "model": "m", "effort": "high", "max_budget_usd": 1.0, "price": PRICE}}})
    with pytest.raises(mod.ConfigError, match="default_tier 'ghost'"):
        merged({"default_tier": "ghost"})


# --- phase 2: the refusals, each with the violation planted ---------------------------------


def test_a_model_without_a_provider_is_refused_naming_both_keys():
    with pytest.raises(ProjectError, match="tiers.worker: sets `model` without `provider`"):
        _project({"tiers": {"worker": {"model": "qwen/qwen3-coder-plus"}}}).model_config()
    ok = _project({"tiers": {"worker": {"provider": "anthropic", "model": "claude-sonnet-5"}}}).model_config()
    assert ok["tiers"]["worker"]["provider"] == "anthropic", "restating anthropic is fine, and is the point"


@pytest.mark.parametrize("var", ["ANTHROPIC_AUTH_TOKEN", "OPENROUTER_API_KEY", "MY_SECRET"])
def test_a_literal_credential_in_a_project_provider_is_refused_and_a_reference_is_not(var):
    import re

    with pytest.raises(ProjectError, match=re.escape(f"providers.openrouter.env.{var}: a credential must be a ${{VAR}} reference")):
        _project({"providers": {"openrouter": {"env": {var: "sk-or-v1-abc123"}}}}).model_config()
    ok = _project({"providers": {"openrouter": {"env": {var: "${OPENROUTER_API_KEY}", "ANTHROPIC_BASE_URL": "https://openrouter.ai/api"}}}}).model_config()
    assert ok["providers"]["openrouter"]["env"]["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api", "a literal base URL is not a secret"


def test_the_merged_config_refuses_a_model_in_a_provider_env_a_templated_model_and_a_bare_alias():
    """These were tests on tiers.yaml; with a project patching the config they are rules
    on the merged one, so the new door cannot reintroduce what the old tests forbade."""
    with pytest.raises(mod.ConfigError, match="provider 'openrouter' names a model in its env"):
        merged({"providers": {"openrouter": {"env": {"ANTHROPIC_MODEL": "x"}}}})
    with pytest.raises(mod.ConfigError, match="tier 'worker' must name a concrete model"):
        merged({"tiers": {"worker": {"provider": "anthropic", "model": "${MODEL}"}}})
    with pytest.raises(mod.ConfigError, match="tier 'worker' names the alias 'opus'"):
        merged({"tiers": {"worker": {"provider": "anthropic", "model": "opus"}}})


@pytest.mark.parametrize(
    "raw, msg",
    [
        ({"ladder": ["worker", "ghost", "strong", "strategic"]}, "ladder names tier\(s\) that do not exist: ghost"),
        ({"ladder": ["worker", "worker", "strong", "strategic"]}, "ladder names tier\(s\) more than once: worker"),
        ({"tiers": {"candidate": {"provider": "deepseek", "model": "m", "effort": "high", "max_budget_usd": 1.0, "price": PRICE}}}, "defined but not on the ladder: candidate"),
        ({"ladder": ["worker", "strong"]}, "not on the ladder"),
    ],
)
def test_the_ladder_and_the_default_are_validated_on_the_merged_config_by_name(raw, msg):
    with pytest.raises(mod.ConfigError, match=msg):
        merged(raw)


def test_the_policy_forced_tier_may_sit_anywhere_on_a_project_ladder():
    """Allowed — and the config check says so out loud (phase 3) rather than refusing."""
    cfg = merged({"ladder": ["worker", "strategic", "strong"]})
    assert cfg["ladder"] == ["worker", "strategic", "strong"]


def test_the_four_keys_are_normative_and_a_dotted_path_under_them_is_refused(tmp_path):
    from models.check_commands import _NORMATIVE, write_repair

    for key in ("tiers", "providers", "default_tier", "ladder"):
        assert key in _NORMATIVE
    for path in ("tiers.worker.model", "providers.openrouter.env.ANTHROPIC_BASE_URL", "default_tier", "ladder"):
        with pytest.raises(ValueError, match="agent-maintained"):
            write_repair("python-uv", path, "x", tmp_path / "harness.yaml")


# --- phase 3: redefinition is loud ------------------------------------------------------------


def _patched(raw):
    """A live load_config that sees `raw` as the project's model config."""
    return lambda *a, **k: mod._validate(mod.merge_model_config(mod._read_config(mod.TIERS_FILE), _project(raw).model_config()), where="test") if k.get("merge_project", True) else mod._validate(mod._read_config(mod.TIERS_FILE))


def test_telemetry_carries_tier_source_project_for_a_redefined_tier_and_plugin_otherwise(monkeypatch):
    monkeypatch.setattr(mod, "load_config", _patched({"tiers": {"worker": {"provider": "anthropic", "model": "claude-sonnet-5", "max_budget_usd": 2.0}}}))
    r = mod.resolve("analyst-survey", project_tiers={})
    assert r.tier == "worker" and r.tier_source == "project" and r.max_budget_usd == 2.0
    assert r.redacted()["tier_source"] == "project"
    assert "(redefined by project)" in str(r)
    r2 = mod.resolve("verifier", project_tiers={})
    assert r2.tier_source == "plugin" and "(redefined" not in str(r2)


def test_the_dispatch_record_carries_the_source(monkeypatch):
    from models.dispatch import dispatch

    monkeypatch.setattr("models.dispatch.require_sandbox", lambda: None)
    monkeypatch.setattr(mod, "load_config", _patched({"tiers": {"strong": {"provider": "anthropic", "model": "claude-opus-5"}}}))
    payload = {"subtype": "success", "is_error": False, "result": "PASS", "total_cost_usd": 0.1, "num_turns": 1,
               "duration_ms": 1000, "session_id": "s", "usage": {"input_tokens": 1, "output_tokens": 1}, "permission_denials": []}
    out = dispatch("verifier", "judge", runner=lambda r, p, **kw: payload)
    assert out.telemetry(task="T-1")["tier_source"] == "project"


def test_the_cost_report_never_shares_a_row_between_a_project_tier_and_the_plugins():
    from models.report import summarise

    events = [
        {"agent": "verifier", "tier": "strong", "provider": "anthropic", "tier_source": "plugin", "cost_usd": 1.0, "turns": 5, "ok": True},
        {"agent": "verifier", "tier": "strong", "provider": "anthropic", "tier_source": "project", "cost_usd": 0.2, "turns": 5, "ok": True},
        {"agent": "verifier", "tier": "strong", "provider": "anthropic", "cost_usd": 1.0, "turns": 5, "ok": True},  # an event from before the field
    ]
    rows = summarise(events)
    by = {(r["tier_source"]): r for r in rows}
    assert by["plugin"]["n"] == 2 and by["project"]["n"] == 1, "an old event with no field is the plugin's"


def test_the_ab_report_flags_an_arm_that_mixes_tier_sources():
    from models.ab_report import summarise

    rows = [{"_run": "1", "_sha": "abc", "agent": "verifier", "cost_usd": 1.0, "tier_source": "plugin"},
            {"_run": "2", "_sha": "abc", "agent": "verifier", "cost_usd": 1.0, "tier_source": "project"}]
    s = summarise({"on": rows})["on"]
    assert s["tier_sources"] == ["plugin", "project"]
    s = summarise({"on": rows[:1]})["on"]
    assert s["tier_sources"] == ["plugin"]


def _check(monkeypatch, raw, model_raw=None):
    import io
    import sys

    from models import check_project

    monkeypatch.setattr(check_project, "load", lambda: _project({"harness": {"version": "0.9.1"}, **raw}))
    monkeypatch.setattr(check_project, "plugin_version", lambda: "0.9.1")
    monkeypatch.setattr(mod, "load_config", _patched(model_raw if model_raw is not None else raw))
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    rc = check_project.main([])
    return rc, out.getvalue(), err.getvalue()


def test_check_project_config_prints_each_redefinition_beside_what_the_plugin_ships_and_names_strategic(monkeypatch):
    rc, out, _ = _check(monkeypatch, {"providers": {"openrouter": {"env": {"ANTHROPIC_BASE_URL": "https://openrouter.ai/api", "ANTHROPIC_AUTH_TOKEN": "${OPENROUTER_API_KEY}"}}},
                                      "tiers": {"worker": {"provider": "openrouter", "model": "qwen/qwen3-coder-plus", "price": PRICE},
                                                "strategic": {"provider": "anthropic", "model": "claude-opus-5"}}})
    assert rc == 0, out
    lines = [ln for ln in out.splitlines() if ln.startswith("models:")]
    assert any("worker = openrouter/qwen/qwen3-coder-plus" in ln and "plugin ships anthropic/claude-sonnet-5" in ln for ln in lines), lines
    assert any("strategic = anthropic/claude-opus-5" in ln and "THE POLICY-FORCED TIER" in ln for ln in lines), lines
    assert any("provider openrouter = env [ANTHROPIC_AUTH_TOKEN, ANTHROPIC_BASE_URL]" in ln for ln in lines), lines


def test_check_project_config_is_silent_without_a_redefinition_and_prints_routing_with_provenance(monkeypatch):
    rc, out, _ = _check(monkeypatch, {})
    assert rc == 0 and not [ln for ln in out.splitlines() if ln.startswith(("models:", "routing:"))]
    rc, out, err = _check(monkeypatch, {"ladder": ["worker", "strategic", "strong"]})
    assert rc == 0
    assert "routing: default_tier=strong  ladder=[worker, strategic, strong]  (plugin default_tier, project ladder)" in out
    assert "not last on the project's ladder" in out + err, "allowed, and said out loud"


# --- what a dispatch actually cost, where the CLI cannot know --------------------------------


def test_pricing_bills_each_token_class_at_its_own_rate_and_halves_off_peak():
    """Measured (2026-09-23): the CLI reported a flat $5.00/Mtok of input for DeepSeek —
    35,335 tokens → $0.17675 — against a published $0.66 off-peak / $1.32 peak. A cost
    series full of that is a fiction, so a tier off Anthropic declares its rates."""
    import datetime as dt

    from models import pricing

    price = {"input_per_mtok": 1.32, "output_per_mtok": 3.96, "cache_read_per_mtok": 0.044,
             "off_peak_multiplier": 0.5, "peak_utc": ["01:00-04:00", "06:00-10:00"]}
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_tokens": 1_000_000}
    peak = dt.datetime(2026, 9, 23, 7, 0, tzinfo=dt.timezone.utc)      # Wednesday, in a peak window
    off = dt.datetime(2026, 9, 23, 12, 0, tzinfo=dt.timezone.utc)      # Wednesday, outside one
    weekend = dt.datetime(2026, 9, 26, 7, 0, tzinfo=dt.timezone.utc)   # Saturday, in the window's hours
    usd, how = pricing.cost(price, usage, peak)
    assert usd == round(1.32 + 3.96 + 0.044, 6) and "peak" in how and "off-peak" not in how
    assert pricing.cost(price, usage, off)[0] == round((1.32 + 3.96 + 0.044) / 2, 6)
    assert pricing.cost(price, usage, weekend)[0] == round((1.32 + 3.96 + 0.044) / 2, 6), "weekends are off-peak"
    # A cache write with no declared rate is billed at the input rate — never silently free.
    assert pricing.cost(price, {"cache_creation_tokens": 1_000_000}, peak)[0] == 1.32
    # No windows declared: always the full rate, never a silent discount.
    assert pricing.cost({"input_per_mtok": 2.0, "output_per_mtok": 4.0}, {"input_tokens": 1_000_000}, off)[0] == 2.0


def test_the_event_says_which_number_it_is_and_an_anthropic_tier_still_uses_the_sdks(monkeypatch):
    from models.dispatch import dispatch

    monkeypatch.setattr("models.dispatch.require_sandbox", lambda: None)
    payload = {"subtype": "success", "is_error": False, "result": "ok", "total_cost_usd": 4.2, "num_turns": 1,
               "duration_ms": 1000, "session_id": "s",
               "usage": {"input_tokens": 1_000_000, "output_tokens": 0}, "permission_denials": []}

    t = dispatch("analyst-survey", "x", runner=lambda r, p, **kw: payload).telemetry(task="T-1")
    assert t["cost_source"] == "sdk" and t["cost_usd"] == 4.2, "Anthropic: the vendor's own accounting"

    monkeypatch.setattr(mod, "load_config", _patched({"tiers": {"worker": {"provider": "deepseek", "model": "deepseek-v4-pro",
                                                                          "price": {"input_per_mtok": 1.32, "output_per_mtok": 3.96,
                                                                                    "off_peak_multiplier": 0.5, "peak_utc": ["01:00-04:00"]}}}}))
    t = dispatch("analyst-survey", "x", runner=lambda r, p, **kw: payload).telemetry(task="T-1")
    assert t["cost_source"].startswith("priced ("), t["cost_source"]
    assert t["cost_usd"] in (1.32, 0.66), f"the tier's own rate, not the SDK's $4.20: {t['cost_usd']}"
    assert "Mtok" in t["cost_source"], "the record says at what rate"


def test_billing_says_which_pocket_and_the_reports_never_sum_them():
    """A subscription-dollar consumed is real — the allowance is finite and the work stops
    when it is gone — but it is not a metered dollar. Summing them states a number neither
    pocket paid, so both reports total them apart."""
    from models.ab_report import summarise as ab_summarise
    from models.report import summarise as cost_summarise

    events = [
        {"agent": "verifier", "tier": "strong", "provider": "anthropic", "billing": "subscription",
         "cost_source": "sdk", "cost_usd": 2.0, "turns": 5, "ok": True},
        {"agent": "fullstack-engineer", "tier": "worker", "provider": "deepseek", "billing": "metered",
         "cost_source": "priced (peak: …)", "cost_usd": 0.5, "turns": 5, "ok": True},
    ]
    rows = {r["agent"]: r for r in cost_summarise(events)}
    assert rows["verifier"]["billing"] == "subscription" and rows["verifier"]["cost_source"] == "sdk"
    assert rows["fullstack-engineer"]["billing"] == "metered" and rows["fullstack-engineer"]["cost_source"] == "priced"

    arm = ab_summarise({"on": [{**e, "_run": "1", "_sha": "abc"} for e in events]})["on"]
    assert arm["by_pocket"] == {"metered": 0.5, "subscription": 2.0}
    assert arm["cost_sources"] == ["priced", "sdk"], "a mixed arm is flagged, not averaged"


def test_a_providers_billing_is_validated_and_defaults_to_metered():
    import re

    from models.resolve import resolve

    assert resolve("verifier").billing == "metered", "the conservative default: real money"
    with pytest.raises(ProjectError, match=re.escape("providers.deepseek.billing: must be 'metered' or 'subscription'")):
        _project({"providers": {"deepseek": {"billing": "free"}}}).model_config()
    ok = _project({"providers": {"anthropic": {"billing": "subscription"}}}).model_config()
    assert ok["providers"]["anthropic"]["billing"] == "subscription"
