"""Cost levers — each a switch, off until measured, and recorded on every dispatch.

The point of a switch is that an A/B can flip it per arm without a plugin release and
read the series afterwards knowing which arm each row was. So: precedence, validation,
that each lever reaches the one place it acts, and that a dispatch event says which
levers were on.
"""

from __future__ import annotations

import pytest

from models import levers
from models.project import Project, ProjectError


def _project(raw):
    return Project(name="x", slug="x", stacks=(), paths={}, areas=(), security={}, raw=raw)


# --- precedence and validation -----------------------------------------------------------


def test_defaults_are_off(monkeypatch):
    for v in levers._ENV.values():
        monkeypatch.delenv(v, raising=False)
    assert levers.snapshot(block={}) == {
        "cache_ttl": None, "static_prefix": False, "stagger_seconds": 0, "task_budget": None, "preload": (),
    }


def test_the_project_block_beats_the_default_and_the_environment_beats_the_block(monkeypatch):
    monkeypatch.delenv("MAD_HARNESS_CACHE_TTL", raising=False)
    assert levers.lever("cache_ttl", block={"cache_ttl": "1h"}) == "1h"
    monkeypatch.setenv("MAD_HARNESS_CACHE_TTL", "5m")
    assert levers.lever("cache_ttl", block={"cache_ttl": "1h"}) == "5m"


def test_environment_values_are_parsed_not_trusted(monkeypatch):
    monkeypatch.setenv("MAD_HARNESS_STATIC_PREFIX", "yes")
    assert levers.lever("static_prefix", block={}) is True
    monkeypatch.setenv("MAD_HARNESS_STATIC_PREFIX", "0")
    assert levers.lever("static_prefix", block={}) is False
    monkeypatch.setenv("MAD_HARNESS_STAGGER_SECONDS", "8")
    assert levers.lever("stagger_seconds", block={}) == 8
    monkeypatch.setenv("MAD_HARNESS_CACHE_TTL", "2h")
    with pytest.raises(ValueError, match="5m, 1h"):
        levers.lever("cache_ttl", block={})


@pytest.mark.parametrize(
    "raw",
    [
        {"dispatch": "5m"},
        {"dispatch": {"cache_ttl": "10m"}},
        {"dispatch": {"static_prefix": "true"}},
        {"dispatch": {"stagger_seconds": -1}},
        {"dispatch": {"stagger": 5}},  # a misspelt key must not be silently ignored
    ],
)
def test_a_malformed_dispatch_block_fails_the_config_check(raw):
    with pytest.raises(ProjectError):
        _project(raw).dispatch()


def test_a_valid_dispatch_block_round_trips():
    assert _project({"dispatch": {"cache_ttl": "5m", "static_prefix": True, "stagger_seconds": 8}}).dispatch() == {
        "cache_ttl": "5m", "static_prefix": True, "stagger_seconds": 8
    }
    assert _project({}).dispatch() == {}


# --- each lever reaches the one place it acts ----------------------------------------------


def test_the_ttl_reaches_the_cli_environment_only_when_set(monkeypatch):
    from models import dispatch as mod
    from models.resolve import resolve

    monkeypatch.setattr("models.levers._project_block", lambda: {})
    monkeypatch.delenv("MAD_HARNESS_CACHE_TTL", raising=False)
    r = resolve("verifier")
    assert "CLAUDE_CODE_PROMPT_CACHE_TTL" not in mod.build_env(r, base={})
    monkeypatch.setenv("MAD_HARNESS_CACHE_TTL", "5m")
    assert mod.build_env(r, base={})["CLAUDE_CODE_PROMPT_CACHE_TTL"] == "5m"


def test_the_static_prefix_reaches_the_sdk_as_the_preset_with_dynamic_sections_excluded(monkeypatch):
    from models.resolve import resolve

    monkeypatch.setattr("models.levers._project_block", lambda: {})
    monkeypatch.delenv("MAD_HARNESS_STATIC_PREFIX", raising=False)
    assert resolve("verifier").sdk_options(cwd=".").system_prompt is None, "off is today's behaviour"
    monkeypatch.setenv("MAD_HARNESS_STATIC_PREFIX", "1")
    sp = resolve("verifier").sdk_options(cwd=".").system_prompt
    assert sp == {"type": "preset", "preset": "claude_code", "exclude_dynamic_sections": True}


def test_a_tier_task_budget_reaches_the_sdk_and_its_absence_is_none(monkeypatch):
    from models import resolve as mod

    r = mod.resolve("verifier")
    assert r.task_budget_tokens is None and r.sdk_options(cwd=".").task_budget is None
    with_budget = mod.Resolved(**{**r.__dict__, "task_budget_tokens": 400_000})
    assert with_budget.sdk_options(cwd=".").task_budget == {"total": 400_000}


def test_every_dispatch_event_says_which_levers_were_on_and_which_experiment(monkeypatch):
    from models.dispatch import dispatch

    monkeypatch.setattr("models.dispatch.require_sandbox", lambda: None)
    monkeypatch.setattr("models.levers._project_block", lambda: {})
    monkeypatch.setenv("MAD_HARNESS_STATIC_PREFIX", "1")
    monkeypatch.setenv("MAD_HARNESS_EXPERIMENT", "static_prefix:on:3")
    payload = {"subtype": "success", "is_error": False, "result": "ok", "total_cost_usd": 0.1,
               "num_turns": 1, "duration_ms": 1, "session_id": "s", "usage": {}, "permission_denials": []}
    t = dispatch("verifier", "x", runner=lambda *a, **k: payload).telemetry()
    assert t["experiment"] == "static_prefix:on:3"
    assert t["levers"] == {
        "cache_ttl": None, "static_prefix": True, "stagger_seconds": 0, "task_budget": None, "preload": (),
    }


def test_a_preloaded_skill_is_appended_to_the_prompt_in_full_and_a_missing_one_is_an_error(monkeypatch):
    from models import dispatch as mod

    monkeypatch.delenv("MAD_HARNESS_PRELOAD", raising=False)
    assert "Preloaded skill" not in mod.with_context("do x", None)
    monkeypatch.setenv("MAD_HARNESS_PRELOAD", "evidence-gathering")
    out = mod.with_context("do x", None)
    assert "## Preloaded skill: evidence-gathering" in out
    assert "scan.sh" in out, "the skill's body, not its frontmatter"
    assert not out.split("## Preloaded skill: evidence-gathering")[1].lstrip().startswith("---")
    monkeypatch.setenv("MAD_HARNESS_PRELOAD", "no-such-skill")
    with pytest.raises(mod.DispatchError, match="no-such-skill"):
        mod.with_context("do x", None)


def test_the_projects_task_budget_beats_the_tier_and_the_env_beats_both(monkeypatch):
    from models import resolve as mod

    monkeypatch.delenv("MAD_HARNESS_TASK_BUDGET_TOKENS", raising=False)
    monkeypatch.setattr("models.levers._project_block", lambda: {})
    assert mod._task_budget({"task_budget_tokens": 250_000}) == 250_000
    assert mod._task_budget({}) is None
    monkeypatch.setattr("models.levers._project_block", lambda: {"task_budget_tokens": 900_000})
    assert mod._task_budget({"task_budget_tokens": 250_000}) == 900_000
    monkeypatch.setenv("MAD_HARNESS_TASK_BUDGET_TOKENS", "123456")
    assert mod._task_budget({"task_budget_tokens": 250_000}) == 123_456


def test_the_worker_tier_carries_the_measured_budget_and_the_record_says_so(monkeypatch):
    """0.10.4: cost per run -32% with the spreads apart. The tier default is the finding;
    a dispatch record must show the budget that applied, not only whether one was flipped."""
    from models import resolve as mod

    monkeypatch.delenv("MAD_HARNESS_TASK_BUDGET_TOKENS", raising=False)
    monkeypatch.setattr("models.levers._project_block", lambda: {})
    r = mod.resolve("fullstack-engineer")
    assert r.tier == "worker" and r.task_budget_tokens == 400_000
    assert r.sdk_options(cwd=".").task_budget == {"total": 400_000}
    assert r.redacted()["task_budget_tokens"] == 400_000


def test_a_task_budget_too_small_to_read_the_task_fails_the_config_check():
    with pytest.raises(ProjectError, match="50000"):
        _project({"dispatch": {"task_budget_tokens": 1000}}).dispatch()
    assert _project({"dispatch": {"task_budget_tokens": 900000}}).dispatch() == {"task_budget_tokens": 900000}
