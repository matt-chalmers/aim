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
    ConfigError,
    agent_frontmatter,
    load_config,
    provider_env,
    resolve,
)

CONFIG = {
    "strengths": {
        "mid": {"provider": "anthropic", "model": "sonnet", "thinking": "high"},
        "strong": {"provider": "anthropic", "model": "opus", "thinking": "xhigh"},
        "elite": {"provider": "cheapo", "model": "big", "thinking": "max"},
    },
    "activities": {
        "work.implement": {"agent": "writer", "strengths": ["mid", "strong"], "max_budget_usd": 1.5},
        "verify.impl": {
            "agent": "lens",
            "strengths": ["strong", "elite"],
            "max_budget_usd": 4.0,
            "complex": {"strengths": ["elite"], "max_budget_usd": 8.0},
        },
        "spec.survey": {"agent": "reader", "strengths": ["mid"], "max_budget_usd": 1.0},
    },
    "providers": {
        "anthropic": {"env": {}},
        "cheapo": {
            "env": {
                "ANTHROPIC_BASE_URL": "https://example.invalid",
                "ANTHROPIC_AUTH_TOKEN": "${CHEAPO_KEY}",
            },
            # A model off Anthropic declares its published rates: the CLI cannot price a
            # third-party endpoint, and `_validate` refuses a strength that leaves it guessing.
            # On the PROVIDER's model, so two strengths sharing one model state one rate.
            "models": {"big": {"price": {"input_per_mtok": 1.0, "output_per_mtok": 2.0}}},
        },
    },
}


@pytest.fixture
def agents(tmp_path):
    """A tiny agent dir. No agent declares a strength — an activity does."""
    for name in ("writer", "lens", "reader"):
        (tmp_path / f"{name}.md").write_text(f"---\nname: {name}\n---\nbody\n")
    return tmp_path


# --- the precedence table: TWO ranks, and the project is not one of them -------


def test_the_activity_chooses_and_the_head_of_its_chain_runs(agents):
    r = resolve("writer", activity="work.implement", config=CONFIG, agents_dir=agents)
    assert (r.strength, r.strength_reason) == ("mid", "activity")
    assert r.max_budget_usd == 1.5 and r.activity == "work.implement"


def test_a_complexity_bucket_wins_over_the_activity_level(agents):
    r = resolve("lens", activity="verify.impl", complexity="complex", config=CONFIG, agents_dir=agents)
    assert r.strength == "elite" and r.max_budget_usd == 8.0 and r.complexity == "complex"


def test_a_bucket_that_restates_nothing_inherits_every_field(agents):
    r = resolve("lens", activity="verify.impl", complexity="simple", config=CONFIG, agents_dir=agents)
    assert r.strength == "strong" and r.max_budget_usd == 4.0


def test_an_explicit_strength_outranks_the_activity(agents):
    """A human may deliberately force a strength, up or down, but must say so outright."""
    r = resolve("writer", activity="work.implement", strength="elite", config=CONFIG, agents_dir=agents)
    assert (r.strength, r.strength_reason) == ("elite", "explicit")


def test_high_risk_reads_the_surface_as_complex(agents):
    """A cheap strength must not be reachable for high-risk work by forgetting a flag — but
    the mechanism is the complexity axis, not a second forced-tier concept that would have to
    be kept on a ladder and could be out-ranked by anything above it."""
    r = resolve("lens", activity="verify.impl", high_risk=True, config=CONFIG, agents_dir=agents)
    assert r.complexity == "complex" and r.strength == "elite"


def test_an_explicit_strength_still_beats_high_risk(agents):
    r = resolve("lens", activity="verify.impl", strength="mid", high_risk=True, config=CONFIG, agents_dir=agents)
    assert (r.strength, r.strength_reason) == ("mid", "explicit")


def test_the_budgets_are_optional_and_none_is_a_real_answer(agents):
    """`max_budget_usd` was mandatory on every tier. It is not now, and an absent one must
    read as absent rather than as zero — `ceiling_source: unset` depends on the distinction."""
    import copy

    cfg = copy.deepcopy(CONFIG)
    del cfg["activities"]["spec.survey"]["max_budget_usd"]
    r = resolve("reader", activity="spec.survey", config=cfg, agents_dir=agents)
    assert r.max_budget_usd is None and r.task_budget_tokens is None


# --- refusals: every one of these is a silent mis-route if it does not raise ---


def test_an_unknown_strength_is_refused(agents):
    with pytest.raises(ConfigError, match="unknown strength"):
        resolve("writer", activity="work.implement", strength="turbo", config=CONFIG, agents_dir=agents)


def test_an_unknown_activity_is_refused(agents):
    with pytest.raises(ConfigError, match="unknown activity"):
        resolve("writer", activity="work.nope", config=CONFIG, agents_dir=agents)


def test_an_unknown_complexity_is_refused(agents):
    with pytest.raises(ConfigError, match="unknown complexity"):
        resolve("writer", activity="work.implement", complexity="gnarly", config=CONFIG, agents_dir=agents)


def test_naming_neither_an_activity_nor_a_strength_is_refused(agents):
    with pytest.raises(ConfigError, match="must name either --activity"):
        resolve("writer", config=CONFIG, agents_dir=agents)


def test_an_activity_dispatched_as_the_wrong_agent_is_refused(agents):
    with pytest.raises(ConfigError, match="performed by 'writer', not 'lens'"):
        resolve("lens", activity="work.implement", config=CONFIG, agents_dir=agents)


def test_a_missing_agent_is_refused(agents):
    """The activity's own binding catches it first, which is the better error: it names who
    the activity IS performed by rather than only that a file is absent."""
    with pytest.raises(ConfigError, match="performed by 'writer', not 'ghost'"):
        resolve("ghost", activity="work.implement", config=CONFIG, agents_dir=agents)
    # And with no activity to contradict, the file's absence is still the refusal.
    with pytest.raises(ConfigError, match="no agent definition"):
        resolve("ghost", strength="mid", config=CONFIG, agents_dir=agents)


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda c: c.update(strengths={}), "no strengths"),
        (lambda c: c.update(activities={}), "no activities"),
        (lambda c: c["strengths"]["mid"].pop("model"), "missing 'model'"),
        (lambda c: c["strengths"]["mid"].update(provider="nobody"), "not defined"),
        (lambda c: c["strengths"]["mid"].update(max_budget_usd=1.0), "belongs on the activity"),
        (lambda c: c["activities"]["work.implement"].pop("agent"), "declares no `agent`"),
        (lambda c: c["activities"]["work.implement"].update(strengths=["ghost"]), "names undefined strength"),
        # THE STATIC CHECK: a bucket-only activity that leaves one label unreachable must fail
        # here, not mid-wave when an epic happens to read as that label.
        (lambda c: c["activities"]["spec.survey"].update(
            strengths=None, simple={"strengths": ["mid"]}), "resolves no `strengths` at complexity 'standard'"),
        (lambda c: c["activities"]["work.implement"].update(complex={"nope": 1}), "unknown key"),
        (lambda c: c.update(ladder=["mid", "strong"]), "`ladder:` is gone"),
        (lambda c: c.update(default_tier="strong"), "`default_tier:` is gone"),
        (lambda c: c.update(tiers={"worker": {}}), "became `strengths:`"),
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


def test_a_strength_off_anthropic_must_declare_its_price(tmp_path):
    """Measured: the CLI priced DeepSeek at a flat $5.00/Mtok of input against a published
    $0.66-1.32 (~10x end to end), and that number reaches `cost_usd` in every record, `make
    models-cost` and every A/B report. A strength that leaves the CLI guessing is refused."""
    import copy

    broken = copy.deepcopy(CONFIG)
    del broken["providers"]["cheapo"]["models"]
    path = tmp_path / "strengths.yaml"
    path.write_text(yaml.safe_dump(broken))
    # The error names BOTH halves — the strength with no rate and the model that owes one.
    with pytest.raises(ConfigError, match=r"strength 'elite' resolves to cheapo/big, which declares no `price`"):
        load_config(path)
    try:
        load_config(path)
    except ConfigError as exc:
        assert "providers.cheapo.models.big.price" in str(exc), "the message names where to put it"

    bad = copy.deepcopy(CONFIG)
    bad["providers"]["cheapo"]["models"]["big"]["price"] = {"input_per_mtok": 1.0}
    path.write_text(yaml.safe_dump(bad))
    with pytest.raises(ConfigError, match="missing 'output_per_mtok'"):
        load_config(path)

    typo = copy.deepcopy(CONFIG)
    typo["providers"]["cheapo"]["models"]["big"]["price"]["input_per_mtoken"] = 1.0
    path.write_text(yaml.safe_dump(typo))
    with pytest.raises(ConfigError, match="unknown price key"):
        load_config(path)

    # An Anthropic strength needs none: the SDK's figure is the vendor's own accounting.
    fine = copy.deepcopy(CONFIG)
    # The fixture's model names are aliases, which _validate also refuses; make them concrete.
    fine["strengths"]["strong"]["model"] = "claude-opus-5"
    fine["strengths"]["mid"]["model"] = "claude-sonnet-5"
    path.write_text(yaml.safe_dump(fine))
    assert load_config(path)["strengths"]["mid"].get("price") is None


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
        "lens",
        activity="verify.impl",
        strength="elite",
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


def test_every_real_agent_is_performed_by_at_least_one_activity():
    """An agent nothing can dispatch is dead weight; the check script enforces this too.

    DERIVED FROM THE ACTIVITY CONFIG, never from the agent's name — the binding lives in
    `activities.<id>.agent` and the relationship between the two names is incidental.
    """
    config = load_config()
    bound = {spec.get("agent") for spec in config["activities"].values()}
    for path in sorted(AGENTS_DIR.glob("*.md")):
        assert path.stem in bound, (
            f"{path.stem} is named by no activity, so nothing can dispatch it on a standard "
            f"boundary. Declare one under `activities:`."
        )


def test_no_agent_declares_a_strength_any_more():
    """`model_tier:` is gone. An agent describes what it IS; the activity it performs decides
    what runs it, which is what lets one agent serve two activities at two strengths."""
    for path in sorted(AGENTS_DIR.glob("*.md")):
        fm = agent_frontmatter(path.stem)
        assert "model_tier" not in fm, f"{path.stem} still declares model_tier"


def test_real_frontmatter_matches_the_activity_that_dispatches_it():
    """Native Agent-tool dispatch reads frontmatter; the boundary reads the activity.

    If they disagree the same agent runs a different model depending on how it was called,
    and both paths appear to work.
    """
    config = load_config(merge_project=False)
    for path in sorted(AGENTS_DIR.glob("*.md")):
        acts = [a for a, spec in config["activities"].items() if spec.get("agent") == path.stem]
        if not acts:
            continue
        r = resolve(path.stem, activity=acts[0], config=config)
        if r.provider != "anthropic":
            # The frontmatter reader has no provider concept; a strength the plugin ships at
            # another provider is exempt from the mirror, as check_config exempts it.
            continue
        fm = agent_frontmatter(path.stem)
        assert (fm.get("model"), fm.get("effort")) == (r.model, r.effort), (
            f"{path.stem}: frontmatter {fm.get('model')}/{fm.get('effort')} != "
            f"{acts[0]} -> {r.strength} -> {r.model}/{r.effort}"
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


def test_every_strength_names_a_model_directly():
    """Uniformly, for every provider — that is what makes the dispatcher's
    ``--model`` the single path and keeps a provider swap to one edit."""
    for name, spec in load_config()["strengths"].items():
        assert spec.get("model") and "${" not in str(spec["model"]), (
            f"strength {name!r} must name a concrete model, got {spec.get('model')!r}"
        )


ALIASES = {"opus", "sonnet", "haiku", "fable", "inherit", "default"}


def test_no_strength_names_a_bare_model_alias():
    """An alias resolves differently per dispatch path, silently.

    Measured, and it invalidated a whole parity experiment: with `model: opus`,
    the Agent tool resolved `claude-opus-5[1m]` while the CLI's `--model opus`
    resolved `claude-opus-4-7` — one model generation and 5x the context window
    apart, for the same word. The verdicts differed and the difference was
    attributed to the dispatch path. Only a concrete id means one thing everywhere.
    """
    for name, spec in load_config()["strengths"].items():
        model = str(spec["model"])
        assert model.lower() not in ALIASES, (
            f"strength {name!r} names the alias {model!r}; use a concrete model id "
            f"(e.g. claude-opus-5) so every dispatch path resolves it the same way"
        )
        assert model.startswith("claude-") or "/" in model or "-" in model, (
            f"strength {name!r} model {model!r} does not look like a concrete id"
        )


def test_frontmatter_parsing_survives_a_description_containing_a_colon():
    """verifier-security.md is not strict YAML and does not need to be.

    Claude Code parses it happily. A strict parse here would crash on a valid
    agent, so the lenient fallback is load-bearing — pin it against a refactor
    that "tidies up" by going back to safe_load.
    """
    fm = agent_frontmatter("verifier-security")
    assert fm["name"] == "verifier-security"
    # The VALUES are not pinned here — they are owner decisions that will change. What is
    # pinned is that keys survive the lenient path at all, which a return to safe_load would
    # break. `model:` is the generated mirror and is present on every shipped agent.
    assert fm["model"] and fm["effort"]
    assert fm["description"].count(":") >= 1, "the colon that defeats a strict parse"


def test_a_non_anthropic_strength_is_exempt_from_the_mirror_and_says_so(monkeypatch, capsys):
    """The frontmatter has no provider field: stamping a `qwen/...` id into `model:` would
    hand it to Anthropic on the native path. sync() leaves such an agent alone and main()
    prints the exemption instead of failing it as drift."""
    from models import check_config
    from models import resolve as mod

    shipped = load_config(merge_project=False)
    cfg = {
        **shipped,
        "strengths": {**shipped["strengths"], "mid": {"provider": "deepseek", "model": "deepseek-v4", "thinking": "high"}},
        "providers": {**shipped["providers"], "deepseek": {**shipped["providers"]["deepseek"],
                                                          "models": {"deepseek-v4": {"price": {"input_per_mtok": 1.0, "output_per_mtok": 2.0}}}}},
    }
    monkeypatch.setattr(mod, "load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(check_config, "load_config", lambda *a, **k: cfg)
    assert check_config.sync("analyst-survey") is None, "not stamped"
    rc = check_config.main()
    out = capsys.readouterr().out
    assert "analyst-survey" in out and "frontmatter not mirrored: non-Anthropic provider" in out
    assert rc == 0, out
