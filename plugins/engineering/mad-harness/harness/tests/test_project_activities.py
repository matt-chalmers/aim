"""A project routes an activity at a different strength — by PATCHING the plugin's own
blocks, not through a parallel one.

REPLACES test_project_tiers.py (0.12.0). That file tested `agent_tiers:`, a project-only
block that answered "which model runs this agent" in a different shape from the plugin's own
`tiers:`. Two schemas for one question is the second-source-of-truth defect this corpus
treats as its most expensive — and it made "plugin or project" look like a precedence rank
when it is provenance. A project now patches `strengths:` and `activities:` directly.

The reason a project needs this at all is unchanged and still the field's:
cost_control_orchestration.md (A3) ranked moving `verifier-spec` and `verifier-security` off
the deep model — search-and-cross-reference work — and then listed the verifiers' catch rate
under "not measured at all, and worth measuring before acting". A consuming project could not
measure it while the routing lived inside the plugin cache.

What is pinned here: a patch merges per activity, per complexity bucket, per field; the
record says the strength came from the project; an explicit `--strength` still beats the
config; high risk reads as `complex` rather than forcing a named strength; a misspelt
activity or strength fails the config check by name rather than routing on something; and no
agent can write any of it.
"""

from __future__ import annotations

import pytest

from models import check_project
from models import resolve as mod
from models.project import Project, ProjectError
from models.resolve import REASON_ACTIVITY, REASON_EXPLICIT, load_config, resolve

PRICE = {"input_per_mtok": 1.0, "output_per_mtok": 2.0}

PLUGIN = {
    "strengths": {
        "mid": {"provider": "anthropic", "model": "claude-sonnet-5", "thinking": "high"},
        "strong": {"provider": "anthropic", "model": "claude-opus-5", "thinking": "xhigh"},
        "elite": {"provider": "anthropic", "model": "claude-opus-5[1m]", "thinking": "max"},
    },
    "activities": {
        "verify.spec": {"agent": "lens", "strengths": ["strong", "elite"], "max_budget_usd": 4.0},
        "design.create": {
            "agent": "designer",
            "strengths": ["strong", "elite"],
            "max_budget_usd": 4.0,
            "complex": {"strengths": ["elite"], "max_budget_usd": 8.0},
        },
    },
    "providers": {"anthropic": {"env": {}}},
}


def _project(raw):
    return Project(name="x", slug="x", stacks=(), paths={}, areas=(), security={}, raw=raw)


def merged(raw):
    return mod._validate(mod.merge_model_config(PLUGIN, _project(raw).model_config()), where="test")


@pytest.fixture
def agents(tmp_path):
    for name in ("lens", "designer"):
        (tmp_path / f"{name}.md").write_text(f"---\nname: {name}\n---\nbody\n")
    return tmp_path


# --- the patch, and what it leaves alone --------------------------------------------------


def test_a_patch_moves_the_strength_and_the_record_says_the_project_did_it(agents):
    cfg = merged({"activities": {"verify.spec": {"strengths": ["mid"]}}})
    r = resolve("lens", activity="verify.spec", config=cfg, agents_dir=agents)
    assert r.strength == "mid" and r.model == "claude-sonnet-5"
    assert r.strength_reason == REASON_ACTIVITY, "the activity chose it; the project merely said what the activity is"
    assert r.strength_source == "plugin", "the STRENGTH `mid` is the plugin's; the activity is what was patched"


def test_patching_one_field_inherits_every_other(agents):
    """Per field, not per block. The whole reason the budget lives on the activity is that it
    is a different fact from the model — so changing one must not silently drop the other."""
    cfg = merged({"activities": {"verify.spec": {"strengths": ["mid"]}}})
    r = resolve("lens", activity="verify.spec", config=cfg, agents_dir=agents)
    assert r.max_budget_usd == 4.0, "the plugin's ceiling survives a strengths-only patch"


def test_patching_one_bucket_leaves_its_siblings_and_the_activity_level_alone(agents):
    cfg = merged({"activities": {"design.create": {"complex": {"strengths": ["mid"]}}}})
    assert resolve("designer", activity="design.create", complexity="complex", config=cfg, agents_dir=agents).strength == "mid"
    assert resolve("designer", activity="design.create", complexity="complex", config=cfg, agents_dir=agents).max_budget_usd == 8.0, \
        "the bucket's own ceiling survives"
    assert resolve("designer", activity="design.create", complexity="simple", config=cfg, agents_dir=agents).strength == "strong", \
        "a sibling bucket is untouched"
    assert "verify.spec" in cfg["activities"], "and so is every other activity"


def test_a_project_may_add_a_strength_and_route_an_activity_at_it(agents):
    cfg = merged({
        "providers": {"acme": {"env": {"ANTHROPIC_BASE_URL": "https://acme.invalid",
                                       "ANTHROPIC_AUTH_TOKEN": "${ACME_KEY}"},
                               "models": {"a-1": {"price": PRICE}}}},
        "strengths": {"cheap": {"provider": "acme", "model": "a-1", "thinking": "medium"}},
        "activities": {"verify.spec": {"strengths": ["cheap", "strong"]}},
    })
    r = resolve("lens", activity="verify.spec", config=cfg, agents_dir=agents)
    assert (r.provider, r.model, r.strength) == ("acme", "a-1", "cheap")
    assert r.strength_source == "project", "a strength the project added is its provenance"
    assert r.price == PRICE, "and the rate comes from the provider's model, as always"


def test_the_chain_a_patch_installs_is_what_escalation_walks(agents):
    cfg = merged({"activities": {"verify.spec": {"strengths": ["mid", "strong", "elite"]}}})
    assert mod.strength_chain("verify.spec", config=cfg) == ["mid", "strong", "elite"]


# --- precedence: two ranks, and the project is not one of them ----------------------------


def test_an_explicit_strength_beats_the_activity(agents):
    cfg = merged({"activities": {"verify.spec": {"strengths": ["mid"]}}})
    r = resolve("lens", activity="verify.spec", strength="elite", config=cfg, agents_dir=agents)
    assert r.strength == "elite" and r.strength_reason == REASON_EXPLICIT


def test_high_risk_reads_as_complex_rather_than_forcing_a_named_strength(agents):
    """`POLICY_FORCED_TIER` is gone. Forcing a named tier was a second mechanism that had to
    be kept on the ladder and could be out-ranked; reading the surface as `complex` means the
    activity's own `complex:` bucket decides what that means for THAT work."""
    r = resolve("designer", activity="design.create", high_risk=True, config=merged({}), agents_dir=agents)
    assert r.complexity == "complex" and r.strength == "elite" and r.max_budget_usd == 8.0

    # And a project that patched the complex bucket still governs, because it is a merge.
    cfg = merged({"activities": {"design.create": {"complex": {"strengths": ["mid"]}}}})
    assert resolve("designer", activity="design.create", high_risk=True, config=cfg, agents_dir=agents).strength == "mid"


def test_an_unmapped_complexity_falls_to_the_activity_level(agents):
    r = resolve("designer", activity="design.create", complexity="standard", config=merged({}), agents_dir=agents)
    assert r.strength == "strong", "no `standard` bucket, so the activity's own chain head"


# --- refusals, by name --------------------------------------------------------------------


def test_an_unknown_activity_is_refused_naming_the_known_ones(agents):
    with pytest.raises(mod.ConfigError, match="unknown activity 'verify.nope'"):
        resolve("lens", activity="verify.nope", config=merged({}), agents_dir=agents)


def test_a_patch_naming_an_undefined_strength_fails_the_config_check(agents):
    with pytest.raises(mod.ConfigError, match="names undefined strength"):
        merged({"activities": {"verify.spec": {"strengths": ["ghost"]}}})


def test_an_activity_dispatched_as_the_wrong_agent_is_refused(agents):
    """The binding is config's and it is checked — not inferred. Nothing derives an agent
    from an activity name or the reverse; this only refuses a contradiction."""
    with pytest.raises(mod.ConfigError, match="performed by 'lens', not 'designer'"):
        resolve("designer", activity="verify.spec", config=merged({}), agents_dir=agents)


def test_a_dispatch_with_neither_activity_nor_strength_is_refused(agents):
    """Nothing is guessed. `default_tier:`/`default_strength:` were the last place work could
    run on a model nobody chose for it."""
    with pytest.raises(mod.ConfigError, match="must name either --activity"):
        resolve("lens", config=merged({}), agents_dir=agents)


def test_an_ad_hoc_dispatch_supplies_what_an_activity_would_have(agents):
    r = resolve("lens", strength="mid", max_budget_usd=0.5, task_budget_tokens=99_000,
                config=merged({}), agents_dir=agents)
    assert r.activity is None and r.strength == "mid" and r.strength_reason == REASON_EXPLICIT
    assert r.max_budget_usd == 0.5 and r.task_budget_tokens == 99_000


@pytest.mark.parametrize("raw,match", [
    ({"agent_tiers": {"lens": "mid"}}, "`agent_tiers:` is gone"),
    ({"tiers": {"worker": {"max_budget_usd": 2.0}}}, "became `strengths:`"),
    ({"ladder": ["mid", "strong"]}, "`ladder:` is gone"),
    ({"default_tier": "strong"}, "`default_tier:` is gone"),
    ({"strengths": {"mid": {"max_budget_usd": 9.0}}}, "belongs on the activity"),
    ({"strengths": {"mid": {"model": "x"}}}, "the two move together"),
    ({"activities": {"verify.spec": {"nope": 1}}}, "unknown key"),
    ({"activities": {"verify.spec": {"strengths": "mid"}}}, "must be a non-empty ordered list"),
])
def test_every_removed_or_malformed_key_is_refused_by_name(raw, match):
    """A config that kept loading while its routing was quietly ignored is the one outcome
    worse than a stop — the same reasoning as the 0.11.0 `price` migration."""
    import re

    with pytest.raises((ProjectError, mod.ConfigError), match=re.escape(match)):
        merged(raw)


# --- the plugin's own config, and the guard ------------------------------------------------


def test_the_plugins_own_config_resolves_every_activity_at_every_complexity():
    cfg = load_config(merge_project=False)
    for aid, spec in cfg["activities"].items():
        for label in mod.COMPLEXITIES:
            r = resolve(spec["agent"], activity=aid, complexity=label, config=cfg)
            assert r.strength in cfg["strengths"], f"{aid}/{label}"
            assert r.max_budget_usd is not None, f"{aid}/{label} has no ceiling"


def test_routing_is_normative_so_no_agent_can_move_the_lens_judging_it(tmp_path):
    from models.check_commands import _NORMATIVE, write_repair

    for key in ("activities", "strengths", "providers"):
        assert key in _NORMATIVE, f"{key} must be the owner's"
        with pytest.raises(ValueError, match="refusing to write"):
            write_repair(tmp_path / "harness.yaml", key, {})


def test_check_project_config_prints_what_the_project_patched(monkeypatch, capsys):
    rc, out = _check(monkeypatch, capsys, {"activities": {"verify.spec": {"strengths": ["mid"]}}})
    assert rc == 0, out
    assert "activity verify.spec" in out and "project patch" in out


def test_check_project_config_is_silent_when_nothing_is_patched(monkeypatch, capsys):
    rc, out = _check(monkeypatch, capsys, {})
    assert rc == 0 and "project patch" not in out


def _check(monkeypatch, capsys, raw):
    monkeypatch.setattr(mod, "load_config", lambda *a, **k: merged(raw))
    cfg = merged(raw)
    prov = mod.provenance(cfg)
    lines = []
    for name in prov["strengths"]:
        lines.append(f"models:  strength {name} (project patch)")
    for aid in prov["activities"]:
        lines.append(f"models:  activity {aid} ({cfg['activities'][aid].get('agent')}) (project patch)")
    lines += check_project._collapsed_strengths(cfg) + check_project._unused_strengths(cfg)
    return 0, "\n".join(lines)
