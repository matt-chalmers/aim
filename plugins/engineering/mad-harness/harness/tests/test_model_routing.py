"""Model routing: the precedence chain, and the two ways it can leak or mis-route.

Precedence is the part of a routing system that rots — it gets re-derived in the
dispatcher, in a check and in a test, and then a task runs on a tier nobody chose.
These tests pin the whole table, including the orderings that only matter when two
inputs disagree, because those are the ones a re-implementation gets wrong.
"""

from __future__ import annotations

import pytest
import yaml

from models.resolve import (
    AGENTS_DIR,
    POLICY_FORCED_TIER,
    ConfigError,
    agent_frontmatter,
    load_config,
    provider_env,
    resolve,
)

CONFIG = {
    "default_tier": "strong",
    "tiers": {
        "worker": {
            "provider": "anthropic",
            "model": "sonnet",
            "effort": "high",
            "max_budget_usd": 1.5,
        },
        "strong": {
            "provider": "anthropic",
            "model": "opus",
            "effort": "xhigh",
            "max_budget_usd": 4.0,
        },
        "strategic": {
            "provider": "cheapo",
            "model": "big",
            "effort": "max",
            "max_budget_usd": 8.0,
        },
    },
    "providers": {
        "anthropic": {"env": {}},
        "cheapo": {
            "env": {
                "ANTHROPIC_BASE_URL": "https://example.invalid",
                "ANTHROPIC_AUTH_TOKEN": "${CHEAPO_KEY}",
            }
        },
    },
}


@pytest.fixture
def agents(tmp_path):
    """A tiny agent dir: one tiered, one untiered, one tiered at a bogus tier."""
    (tmp_path / "tiered.md").write_text(
        "---\nname: tiered\nmodel_tier: worker\n---\nbody\n"
    )
    (tmp_path / "untiered.md").write_text("---\nname: untiered\n---\nbody\n")
    (tmp_path / "bogus.md").write_text(
        "---\nname: bogus\nmodel_tier: nope\n---\nbody\n"
    )
    return tmp_path


# --- the precedence table, including the disagreement cases -------------------


def test_agent_default_is_used_when_nothing_else_applies(agents):
    r = resolve("tiered", config=CONFIG, agents_dir=agents)
    assert (r.tier, r.reason) == ("worker", "agent default")


def test_global_default_is_used_when_the_agent_declares_no_tier(agents):
    r = resolve("untiered", config=CONFIG, agents_dir=agents)
    assert (r.tier, r.reason) == ("strong", "global default")


def test_policy_outranks_the_agent_default(agents):
    """A cheap tier must not be reachable for high-risk work by forgetting a flag.

    This is the ordering that matters most: the agent whose *ordinary* work is
    cheap is exactly the agent that will one day be handed an auth change.
    """
    r = resolve("tiered", high_risk=True, config=CONFIG, agents_dir=agents)
    assert (r.tier, r.reason) == (POLICY_FORCED_TIER, "policy: high-risk surface")


def test_explicit_override_outranks_policy(agents):
    """A human may deliberately force a tier down, but must say so outright."""
    r = resolve(
        "tiered",
        override_tier="worker",
        high_risk=True,
        config=CONFIG,
        agents_dir=agents,
    )
    assert (r.tier, r.reason) == ("worker", "explicit override")


def test_override_outranks_the_agent_default_upwards_too(agents):
    r = resolve("tiered", override_tier="strategic", config=CONFIG, agents_dir=agents)
    assert r.tier == "strategic"


# --- refusals: every one of these is a silent mis-route if it does not raise ---


def test_an_unknown_override_tier_is_refused(agents):
    with pytest.raises(ConfigError, match="unknown tier"):
        resolve("tiered", override_tier="turbo", config=CONFIG, agents_dir=agents)


def test_an_agent_declaring_an_undefined_tier_is_refused(agents):
    with pytest.raises(ConfigError, match="not defined"):
        resolve("bogus", config=CONFIG, agents_dir=agents)


def test_a_missing_agent_is_refused(agents):
    with pytest.raises(ConfigError, match="no agent definition"):
        resolve("ghost", config=CONFIG, agents_dir=agents)


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda c: c.update(default_tier="ghost"), "not one of"),
        (lambda c: c.update(tiers={}), "no tiers"),
        (lambda c: c["tiers"]["worker"].pop("model"), "missing 'model'"),
        (lambda c: c["tiers"]["worker"].update(provider="nobody"), "not defined"),
        (lambda c: c["tiers"].pop(POLICY_FORCED_TIER), "policy-forced tier"),
    ],
)
def test_structurally_broken_config_is_refused(tmp_path, mutate, match):
    """A config that half-loads routes work somewhere nobody chose."""
    import copy

    broken = copy.deepcopy(CONFIG)
    mutate(broken)
    path = tmp_path / "tiers.yaml"
    path.write_text(yaml.safe_dump(broken))
    with pytest.raises(ConfigError, match=match):
        load_config(path)


# --- secrets ------------------------------------------------------------------


def test_provider_env_expands_from_the_environment_not_the_file():
    env, missing = provider_env("cheapo", CONFIG, environ={"CHEAPO_KEY": "sk-secret"})
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-secret"
    assert (
        env["ANTHROPIC_BASE_URL"] == "https://example.invalid"
    )  # literals pass through
    assert missing == ()


def test_an_unset_credential_is_reported_not_silently_blank():
    """A blank token yields a 401 at dispatch, which reads as a provider outage."""
    env, missing = provider_env("cheapo", CONFIG, environ={})
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert missing == ("CHEAPO_KEY",)


def test_no_rendering_path_can_emit_a_credential_value(agents):
    """redacted() and __str__ both feed logs, tasks and telemetry.

    A token written into a telemetry task survives in the exported issues.jsonl
    and is therefore committed. Names only, never values — asserted on every
    rendering path rather than the one we happened to think of.
    """
    r = resolve(
        "tiered",
        override_tier="strategic",
        config=CONFIG,
        agents_dir=agents,
        environ={"CHEAPO_KEY": "sk-do-not-leak"},
    )
    assert (
        r.env["ANTHROPIC_AUTH_TOKEN"] == "sk-do-not-leak"
    )  # the subprocess still gets it

    assert "sk-do-not-leak" not in str(r)
    assert "sk-do-not-leak" not in repr(r.redacted())
    assert r.redacted()["env_names"] == ["ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"]


# --- the real repository ------------------------------------------------------


def test_every_real_agent_declares_a_tier_that_resolves():
    """The check script enforces this too; this fails the suite as well as the gate."""
    config = load_config()
    for path in sorted(AGENTS_DIR.glob("*.md")):
        r = resolve(path.stem, config=config)
        assert r.reason == "agent default", (
            f"{path.stem} has no model_tier, so it silently takes the global default"
        )


def test_real_frontmatter_matches_the_tier_it_declares():
    """Native Agent-tool dispatch reads frontmatter; the boundary reads the tier.

    If they disagree the same agent runs a different model depending on how it
    was called, and both paths appear to work.
    """
    config = load_config(merge_project=False)
    for path in sorted(AGENTS_DIR.glob("*.md")):
        r = resolve(path.stem, config=config, project_tiers={})
        if r.provider != "anthropic":
            # The frontmatter reader has no provider concept; a tier the plugin ships
            # at another provider is exempt from the mirror, as check_config exempts it.
            continue
        fm = agent_frontmatter(path.stem)
        assert (fm.get("model"), fm.get("effort")) == (r.model, r.effort), (
            f"{path.stem}: frontmatter {fm.get('model')}/{fm.get('effort')} != "
            f"tier {r.tier} -> {r.model}/{r.effort}"
        )


def test_no_provider_declares_a_model():
    """The tier owns the model; a provider block must not name one too.

    ``ANTHROPIC_MODEL`` in a provider's env would be a second source of truth for
    a value the tier already declares and the dispatcher already passes as
    ``--model``. The two could disagree, and the loser would be dead config that
    still looks live — the exact shape check_config.py exists to forbid between
    frontmatter and tiers.
    """
    config = load_config()
    for name, spec in (config.get("providers") or {}).items():
        keys = set(spec.get("env") or {})
        assert "ANTHROPIC_MODEL" not in keys, (
            f"provider {name!r} names a model in its env; the tier owns that"
        )
        assert not any("MODEL" in k for k in keys), (
            f"provider {name!r} has a model-shaped env key: {sorted(keys)}"
        )


def test_every_tier_names_a_model_directly():
    """Uniformly, for every provider — that is what makes the dispatcher's
    ``--model`` the single path and keeps a provider swap to one edit."""
    for name, spec in load_config()["tiers"].items():
        assert spec.get("model") and "${" not in str(spec["model"]), (
            f"tier {name!r} must name a concrete model, got {spec.get('model')!r}"
        )


ALIASES = {"opus", "sonnet", "haiku", "fable", "inherit", "default"}


def test_no_tier_names_a_bare_model_alias():
    """An alias resolves differently per dispatch path, silently.

    Measured, and it invalidated a whole parity experiment: with `model: opus`,
    the Agent tool resolved `claude-opus-5[1m]` while the CLI's `--model opus`
    resolved `claude-opus-4-7` — one model generation and 5x the context window
    apart, for the same word. The verdicts differed and the difference was
    attributed to the dispatch path. Only a concrete id means one thing everywhere.
    """
    for name, spec in load_config()["tiers"].items():
        model = str(spec["model"])
        assert model.lower() not in ALIASES, (
            f"tier {name!r} names the alias {model!r}; use a concrete model id "
            f"(e.g. claude-opus-5) so every dispatch path resolves it the same way"
        )
        assert model.startswith("claude-") or "/" in model or "-" in model, (
            f"tier {name!r} model {model!r} does not look like a concrete id"
        )


def test_frontmatter_parsing_survives_a_description_containing_a_colon():
    """verifier-security.md is not strict YAML and does not need to be.

    Claude Code parses it happily. A strict parse here would crash on a valid
    agent, so the lenient fallback is load-bearing — pin it against a refactor
    that "tidies up" by going back to safe_load.
    """
    fm = agent_frontmatter("verifier-security")
    assert fm["name"] == "verifier-security"
    # The VALUE is not pinned here — which tier this agent sits in is an owner
    # decision that will change. What is pinned is that the key survives the
    # lenient path at all, which is what a return to safe_load would break.
    assert fm["model_tier"] in load_config()["tiers"]


def test_a_non_anthropic_tier_is_exempt_from_the_mirror_and_says_so(monkeypatch, capsys):
    """The frontmatter has no provider field: stamping a `qwen/...` id into `model:` would
    hand it to Anthropic on the native path. sync() leaves such an agent alone and main()
    prints the exemption instead of failing it as drift."""
    from models import check_config
    from models import resolve as mod

    shipped = load_config(merge_project=False)
    cfg = {**shipped, "tiers": {**shipped["tiers"], "worker": {**shipped["tiers"]["worker"], "provider": "deepseek", "model": "deepseek-v4"}}}
    monkeypatch.setattr(mod, "load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(check_config, "load_config", lambda *a, **k: cfg)
    assert check_config.sync("analyst-survey") is None, "not stamped"
    rc = check_config.main()
    out = capsys.readouterr().out
    assert "analyst-survey" in out and "frontmatter not mirrored: non-Anthropic provider" in out
    assert rc == 0, out
