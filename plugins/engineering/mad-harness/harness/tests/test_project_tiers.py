"""A project moves an agent between tiers without patching the plugin — a switch, not
a default, because the field asked for one.

cost_control_orchestration.md (A3) ranked tier-splitting `verifier-spec` and
`verifier-security` — search-and-cross-reference work, the kind `analyst-survey` already
runs on `worker` — and then listed the verifiers' catch rate under "not measured at all,
and worth measuring before acting". A consuming project could not measure it: the tier
lived in the agent's frontmatter, inside the plugin cache. This block is the A/B switch,
and every row it produces says which arm it was.

What is pinned: the override moves the tier and the record says why; policy still forces a
high-risk dispatch up over it; an explicit override still beats everything; a misspelt
agent or tier fails the config check by name rather than routing on the default; an absent
block changes nothing; and the dispatch telemetry carries the moved tier.
"""

from __future__ import annotations

import pytest

from models import check_project
from models import resolve as mod
from models.project import Project, ProjectError
from models.resolve import POLICY_FORCED_TIER, PROJECT_OVERRIDE, load_config, resolve

CONFIG = {
    "default_tier": "strong",
    "tiers": {
        "worker": {"provider": "anthropic", "model": "claude-sonnet-5", "effort": "high", "max_budget_usd": 1.5},
        "strong": {"provider": "anthropic", "model": "claude-opus-5", "effort": "xhigh", "max_budget_usd": 4.0},
        "strategic": {"provider": "anthropic", "model": "claude-opus-5[1m]", "effort": "max", "max_budget_usd": 8.0},
    },
    "providers": {"anthropic": {"env": {}}},
}


def _project(raw):
    return Project(name="x", slug="x", stacks=(), paths={}, areas=(), security={}, raw=raw)


@pytest.fixture
def agents(tmp_path):
    """One agent on `strong` by its own declaration, one declaring nothing."""
    (tmp_path / "lens.md").write_text("---\nname: lens\nmodel_tier: strong\n---\nbody\n")
    (tmp_path / "untiered.md").write_text("---\nname: untiered\n---\nbody\n")
    return tmp_path


# --- the precedence table, with the new rank in it ----------------------------------------


def test_the_override_moves_the_agent_and_the_reason_names_harness_yaml(agents):
    r = resolve("lens", project_tiers={"lens": "worker"}, config=CONFIG, agents_dir=agents)
    assert (r.tier, r.reason) == ("worker", PROJECT_OVERRIDE)
    assert r.reason == "project override (harness.yaml tiers)", "telemetry carries this string verbatim"
    assert r.model == "claude-sonnet-5", "the tier's model, not the agent's frontmatter"
    assert r.redacted()["reason"] == PROJECT_OVERRIDE


def test_the_override_beats_the_global_default_as_well_as_the_agents_own(agents):
    r = resolve("untiered", project_tiers={"untiered": "worker"}, config=CONFIG, agents_dir=agents)
    assert (r.tier, r.reason) == ("worker", PROJECT_OVERRIDE)


def test_policy_still_forces_a_high_risk_dispatch_up_over_the_project_override(agents):
    """The one ordering that must not change. A project can move a lens down for its
    ordinary work; it cannot, by configuration, put a security-sensitive diff in front of
    the cheap tier. Lowering a high-risk dispatch still takes the explicit override, said
    outright, per dispatch."""
    r = resolve("lens", high_risk=True, project_tiers={"lens": "worker"}, config=CONFIG, agents_dir=agents)
    assert (r.tier, r.reason) == (POLICY_FORCED_TIER, "policy: high-risk surface")


def test_an_explicit_override_still_wins_over_policy_and_the_project(agents):
    r = resolve(
        "lens", override_tier="worker", high_risk=True,
        project_tiers={"lens": "strategic"}, config=CONFIG, agents_dir=agents,
    )
    assert (r.tier, r.reason) == ("worker", "explicit override")


def test_an_agent_the_block_does_not_name_keeps_its_own_default(agents):
    r = resolve("lens", project_tiers={"untiered": "worker"}, config=CONFIG, agents_dir=agents)
    assert (r.tier, r.reason) == ("strong", "agent default")


def test_an_override_handed_in_directly_is_still_checked_against_the_tiers(agents):
    """`Project.tiers` validates the block, but the argument can bypass it; a bare
    KeyError from inside the resolver would be a routing failure with no name on it."""
    from models.resolve import ConfigError

    with pytest.raises(ConfigError, match="unknown tier 'turbo'"):
        resolve("lens", project_tiers={"lens": "turbo"}, config=CONFIG, agents_dir=agents)


# --- the block is validated, by name, before any wave ------------------------------------


def test_an_unknown_agent_in_the_block_fails_the_config_check_naming_it(agents):
    with pytest.raises(ProjectError, match=r"tiers\.ghost: no such agent") as exc:
        _project({"tiers": {"ghost": "worker"}}).tiers(config=CONFIG, agents_dir=agents)
    assert "lens" in str(exc.value) and "untiered" in str(exc.value), "the fix is listed, not left to guess"


def test_an_unknown_tier_in_the_block_fails_the_config_check_naming_it(agents):
    with pytest.raises(ProjectError, match=r"tiers\.lens is 'turbo'") as exc:
        _project({"tiers": {"lens": "turbo"}}).tiers(config=CONFIG, agents_dir=agents)
    assert "worker, strong, strategic" in str(exc.value)
    with pytest.raises(ProjectError, match=r"tiers\.lens is None"):
        _project({"tiers": {"lens": None}}).tiers(config=CONFIG, agents_dir=agents)


@pytest.mark.parametrize("raw", ["worker", ["lens"], 3])
def test_a_block_that_is_not_a_map_is_refused_with_the_shape_it_wanted(agents, raw):
    with pytest.raises(ProjectError, match="map of agent -> tier"):
        _project({"tiers": raw}).tiers(config=CONFIG, agents_dir=agents)


def test_a_valid_block_round_trips_and_the_guard_can_pass(agents):
    """The refusals above would pass vacuously if `tiers()` refused everything."""
    assert _project({"tiers": {"lens": "worker", "untiered": "strategic"}}).tiers(
        config=CONFIG, agents_dir=agents
    ) == {"lens": "worker", "untiered": "strategic"}


# --- absent means unchanged ---------------------------------------------------------------


def test_an_absent_or_empty_block_is_no_overrides_and_no_error(agents):
    assert _project({}).tiers(config=CONFIG, agents_dir=agents) == {}
    assert _project({"tiers": {}}).tiers(config=CONFIG, agents_dir=agents) == {}
    assert _project({"tiers": None}).tiers(config=CONFIG, agents_dir=agents) == {}


def test_the_plugins_own_config_declares_no_overrides_so_every_real_agent_keeps_its_default():
    """The plugin ships its defaults; the switch lives in the consuming project. If this
    ever fails, the plugin has started A/B-ing itself, which is the drift the switch
    exists to avoid."""
    assert mod.project_tier_overrides() == {}
    r = resolve("verifier-spec")
    assert r.reason == "agent default" and r.tier == "strong"


def test_passing_an_empty_map_asks_for_the_plugins_defaults_whatever_the_project_says(monkeypatch, agents):
    """`check-model-config.sh` uses this: it judges the plugin's two readers against
    each other, and a project's deliberate override must not read as drift."""
    monkeypatch.setattr(mod, "project_tier_overrides", lambda **k: {"lens": "worker"})
    assert resolve("lens", config=CONFIG, agents_dir=agents).tier == "worker"
    assert resolve("lens", project_tiers={}, config=CONFIG, agents_dir=agents).tier == "strong"


# --- read from harness.yaml when nothing is passed ---------------------------------------


def test_the_overrides_are_read_from_the_projects_harness_yaml(tmp_path, monkeypatch, agents):
    cfg = tmp_path / "harness.yaml"
    cfg.write_text("name: x\nslug: x\nareas: []\ntiers: {lens: worker}\n")
    monkeypatch.setattr("models.project.PROJECT_FILE", cfg)
    assert mod.project_tier_overrides(config=CONFIG, agents_dir=agents) == {"lens": "worker"}
    r = resolve("lens", config=CONFIG, agents_dir=agents)
    assert (r.tier, r.reason) == ("worker", PROJECT_OVERRIDE)


def test_no_harness_yaml_at_all_is_the_old_behaviour_not_an_error(tmp_path, monkeypatch, agents):
    monkeypatch.setattr("models.project.PROJECT_FILE", tmp_path / "absent.yaml")
    assert mod.project_tier_overrides(config=CONFIG, agents_dir=agents) == {}
    assert resolve("lens", config=CONFIG, agents_dir=agents).reason == "agent default"


def test_a_malformed_block_stops_the_dispatch_rather_than_routing_on_the_default(tmp_path, monkeypatch, capsys):
    """A misspelt agent that fell through to the agent default would leave the A/B arm
    running on the tier it meant to move off, recorded as if it had moved. So the
    dispatcher refuses, by name, before spending anything — dry-run included."""
    from models import dispatch as dmod

    cfg = tmp_path / "harness.yaml"
    cfg.write_text("name: x\nslug: x\nareas: []\ntiers: {verifer-spec: worker}\n")
    monkeypatch.setattr("models.project.PROJECT_FILE", cfg)
    with pytest.raises(ProjectError, match=r"tiers\.verifer-spec: no such agent"):
        resolve("verifier-spec")
    pf = tmp_path / "p.txt"
    pf.write_text("do the thing")
    assert dmod.main(["verifier-spec", "--prompt-file", str(pf), "--dry-run"]) == 2
    assert "FAIL: tiers.verifer-spec: no such agent" in capsys.readouterr().err


# --- the record says which arm ------------------------------------------------------------


def test_the_dispatch_telemetry_carries_the_overridden_tier_and_why(monkeypatch):
    """`make models-cost` groups by tier and the A/B report by reason; a row that said
    `strong` for a dispatch that ran on `worker` would put the arm's cost on the wrong
    side of the comparison."""
    from models.dispatch import dispatch

    monkeypatch.setattr("models.dispatch.require_sandbox", lambda: None)
    monkeypatch.setattr(mod, "project_tier_overrides", lambda **k: {"verifier-spec": "worker"})
    payload = {
        "subtype": "success", "is_error": False, "result": "PASS", "total_cost_usd": 0.4,
        "num_turns": 9, "duration_ms": 30_000, "session_id": "s-1",
        "usage": {"input_tokens": 100, "output_tokens": 50}, "permission_denials": [],
    }
    out = dispatch("verifier-spec", "judge it", runner=lambda r, prompt, **kw: payload)
    t = out.telemetry(task="T-1")
    assert t["tier"] == "worker" and t["reason"] == PROJECT_OVERRIDE
    assert t["model"] == load_config()["tiers"]["worker"]["model"]
    assert out.resolved.max_budget_usd == float(load_config()["tiers"]["worker"]["max_budget_usd"])


# --- the config check shows the arm ------------------------------------------------------


def _check(monkeypatch, raw):
    import io
    import sys

    monkeypatch.setattr(check_project, "load", lambda: _project({"harness": {"version": "0.9.1"}, **raw}))
    monkeypatch.setattr(check_project, "plugin_version", lambda: "0.9.1")
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    rc = check_project.main([])
    return rc, out.getvalue(), err.getvalue()


def test_check_project_config_prints_the_overrides_on_one_line_and_calls_them_overrides(monkeypatch):
    rc, out, _ = _check(monkeypatch, {"tiers": {"verifier-spec": "worker", "verifier-security": "worker"}})
    assert rc == 0
    [line] = [ln for ln in out.splitlines() if ln.startswith("tiers:")]
    assert line == "tiers:   verifier-security->worker, verifier-spec->worker  (project overrides)"


def test_check_project_config_is_silent_about_tiers_when_none_are_overridden(monkeypatch):
    rc, out, _ = _check(monkeypatch, {})
    assert rc == 0 and not [ln for ln in out.splitlines() if ln.startswith("tiers:")]


def test_check_project_config_fails_on_a_bad_override_naming_it(monkeypatch):
    rc, _, err = _check(monkeypatch, {"tiers": {"verifier-spec": "turbo"}})
    assert rc == 1 and "tiers.verifier-spec is 'turbo'" in err


# --- the switch is the owner's ------------------------------------------------------------


def test_the_tiers_block_is_normative_so_no_agent_can_move_the_lens_judging_it(tmp_path):
    """Moving the lens about to judge a worker down a tier is a quieter way of switching
    it off; the repair mechanism must refuse the key like it refuses `security`."""
    from models.check_commands import _NORMATIVE, write_repair

    assert "tiers" in _NORMATIVE
    with pytest.raises(ValueError, match="agent-maintained"):
        write_repair("python-uv", "tiers.verifier-spec", "worker", tmp_path / "harness.yaml")


def test_check_model_config_judges_the_plugins_defaults_not_the_projects_arm(monkeypatch, capsys):
    """Run from a project that has moved a lens, the frontmatter/tier comparison would
    otherwise report the deliberate override as drift and fail a correct config."""
    from models import check_config

    monkeypatch.setattr(mod, "project_tier_overrides", lambda **k: {"verifier-spec": "worker"})
    assert resolve("verifier-spec").tier == "worker", "the override is live for the dispatcher"
    assert check_config.main() == 0, capsys.readouterr().err
    assert "verifier-spec          strong" in capsys.readouterr().out
