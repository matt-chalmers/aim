"""Project-owned model config (0.10.30): a consuming project patches the plugin's tier
DEFINITIONS — provider, model, effort, budget — adds tiers, extends or redefines providers,
in its own harness.yaml, by patching the same blocks the plugin ships.

Two questions kept apart: `agent_tiers:` answers which tier an agent runs on (selection);
`tiers:` here answers what a tier is (definition). Maps patch, scalars and lists replace,
and the MERGED config is what gets validated — validating before the merge would judge a
config nobody runs.
"""

from __future__ import annotations

import pytest

from models import resolve as mod
from models.project import Project, ProjectError

#: A model off Anthropic must declare its published rates — the CLI cannot price a
#: third-party endpoint (measured: $5.00/Mtok for DeepSeek). See models/pricing.py.
PRICE = {"input_per_mtok": 1.32, "output_per_mtok": 3.96, "cache_read_per_mtok": 0.044}

PLUGIN = {
    "strengths": {
        "mid": {"provider": "anthropic", "model": "claude-sonnet-5", "thinking": "high"},
        "strong": {"provider": "anthropic", "model": "claude-opus-5[1m]", "thinking": "xhigh"},
        "elite": {"provider": "anthropic", "model": "claude-opus-5[1m]", "thinking": "max"},
    },
    "activities": {
        "work.implement": {"agent": "grunt", "strengths": ["mid", "strong"], "max_budget_usd": 3.0,
                           "task_budget_tokens": 400000},
        "verify.impl": {"agent": "lens", "strengths": ["strong", "elite"], "max_budget_usd": 4.0},
        "design.create": {"agent": "designer", "strengths": ["strong", "elite"], "max_budget_usd": 4.0,
                          "complex": {"strengths": ["elite"], "max_budget_usd": 8.0}},
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
    for name in ("lens", "grunt", "designer", "analyst-survey"):
        (tmp_path / f"{name}.md").write_text(f"---\nname: {name}\n---\nbody\n")
    return tmp_path


def merged(raw):
    return mod._validate(mod.merge_model_config(PLUGIN, _project(raw).model_config()), where="test")


# --- the merge -----------------------------------------------------------------------------


def test_patching_only_the_budget_inherits_provider_model_and_effort(agents):
    cfg = merged({"activities": {"work.implement": {"max_budget_usd": 9.5}}})
    assert cfg["activities"]["work.implement"]["max_budget_usd"] == 9.5
    assert cfg["activities"]["work.implement"]["strengths"] == ["mid", "strong"], "the chain survives a budget-only patch"
    assert cfg["activities"]["work.implement"]["task_budget_tokens"] == 400000, "and so does the told budget"
    r = mod.resolve("grunt", activity="work.implement", config=cfg, agents_dir=agents)
    assert r.max_budget_usd == 9.5 and r.model == "claude-sonnet-5"


def test_overriding_anthropics_env_merges_per_key_not_the_whole_map():
    plugin = {**PLUGIN, "providers": {**PLUGIN["providers"], "anthropic": {"env": {"ANTHROPIC_BASE_URL": "https://proxy.example", "KEEP": "1"}}}}
    cfg = mod.merge_model_config(plugin, _project({"providers": {"anthropic": {"env": {"ANTHROPIC_BASE_URL": "https://other.example"}}}}).model_config())
    assert cfg["providers"]["anthropic"]["env"] == {"ANTHROPIC_BASE_URL": "https://other.example", "KEEP": "1"}
    assert "deepseek" in cfg["providers"], "other providers survive"


def test_merge_project_false_is_the_plugins_shipped_config_whatever_the_project_says(monkeypatch):
    monkeypatch.setattr(mod, "_project_model_config", lambda: {"strengths": {"mid": {"provider": "anthropic", "model": "qwen/x"}}})
    shipped = mod.load_config(merge_project=False)
    assert shipped["strengths"]["mid"]["model"] == "claude-sonnet-5"
    live = mod.load_config()
    assert live["strengths"]["mid"]["model"] == "qwen/x"


def test_no_project_config_at_all_is_todays_behaviour(monkeypatch):
    monkeypatch.setattr(mod, "_project_model_config", lambda: {})
    assert mod.load_config()["strengths"]["mid"]["model"] == "claude-sonnet-5"
    assert _project({}).model_config() == {}


# --- shape validation in Project.model_config ----------------------------------------------


@pytest.mark.parametrize(
    "raw, msg",
    [
        ({"strengths": ["mid"]}, "strengths: must be a map"),
        ({"strengths": {"mid": "fast"}}, "strengths.mid: must be a map"),
        ({"strengths": {"mid": {"modle": "x"}}}, "strengths.mid: unknown key(s) modle"),
        ({"providers": {"openrouter": {"url": "x"}}}, "providers.openrouter: unknown key(s) url"),
        ({"providers": {"openrouter": {"env": "x"}}}, "providers.openrouter.env: must be a map"),
        ({"default_tier": "strong"}, "`default_tier:` is gone"),
        ({"ladder": ["mid"]}, "`ladder:` is gone"),
    ],
)
def test_a_malformed_block_is_refused_by_name(raw, msg):
    import re

    with pytest.raises(ProjectError, match=re.escape(msg)):
        _project(raw).model_config()


def test_the_merged_config_is_what_is_validated():
    """A tier the project adds with a provider nobody defined fails the merged check,
    naming the tier and the provider — before the merge it would have looked fine."""
    with pytest.raises(mod.ConfigError, match="strength 'candidate' names provider 'nowhere'"):
        merged({"strengths": {"candidate": {"provider": "nowhere", "model": "m", "thinking": "high"}}})
    with pytest.raises(ProjectError, match="`default_tier:` is gone"):
        merged({"default_tier": "ghost"})


# --- phase 2: the refusals, each with the violation planted ---------------------------------


def test_a_model_without_a_provider_is_refused_naming_both_keys():
    with pytest.raises(ProjectError, match="strengths.mid: sets `model` without `provider`"):
        _project({"strengths": {"mid": {"model": "qwen/qwen3-coder-plus"}}}).model_config()
    ok = _project({"strengths": {"mid": {"provider": "anthropic", "model": "claude-sonnet-5"}}}).model_config()
    assert ok["strengths"]["mid"]["provider"] == "anthropic", "restating anthropic is fine, and is the point"


@pytest.mark.parametrize("var", ["ANTHROPIC_AUTH_TOKEN", "OPENROUTER_API_KEY", "MY_SECRET"])
def test_a_literal_credential_in_a_project_provider_is_refused_and_a_reference_is_not(var):
    import re

    with pytest.raises(ProjectError, match=re.escape(f"providers.openrouter.env.{var}: a credential must be a ${{VAR}} reference")):
        _project({"providers": {"openrouter": {"env": {var: "sk-or-v1-abc123"}}}}).model_config()
    ok = _project({"providers": {"openrouter": {"env": {var: "${OPENROUTER_API_KEY}", "ANTHROPIC_BASE_URL": "https://openrouter.ai/api"}}}}).model_config()
    assert ok["providers"]["openrouter"]["env"]["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api", "a literal base URL is not a secret"


def test_the_merged_config_refuses_a_model_in_a_provider_env_a_templated_model_and_a_bare_alias():
    """These were tests on the plugin's own file; with a project patching the config they are rules
    on the merged one, so the new door cannot reintroduce what the old tests forbade."""
    # Refused at the PROJECT layer now, which is earlier and better scoped — the merged
    # validator still carries the same rule for the plugin's own file. Either is a refusal.
    with pytest.raises((ProjectError, mod.ConfigError), match="names? a model"):
        merged({"providers": {"openrouter": {"env": {"ANTHROPIC_MODEL": "x"}}}})
    with pytest.raises(mod.ConfigError, match="strength 'mid' must name a concrete model"):
        merged({"strengths": {"mid": {"provider": "anthropic", "model": "${MODEL}"}}})
    with pytest.raises(mod.ConfigError, match="strength 'mid' names the alias 'opus'"):
        merged({"strengths": {"mid": {"provider": "anthropic", "model": "opus"}}})



def test_the_routing_keys_are_normative_and_a_dotted_path_under_them_is_refused(tmp_path):
    from models.check_commands import _NORMATIVE, write_repair

    for key in ("strengths", "activities", "providers"):
        assert key in _NORMATIVE
    for path in ("strengths.mid.model", "activities.work.implement.strengths", "providers.openrouter.env.ANTHROPIC_BASE_URL"):
        with pytest.raises(ValueError, match="agent-maintained"):
            (tmp_path / "harness.yaml").write_text("name: x\nslug: x\n")
            write_repair("python-uv", path, "x", tmp_path / "harness.yaml")


# --- phase 3: redefinition is loud ------------------------------------------------------------


def _patched(raw):
    """A live load_config that sees `raw` as the project's model config."""
    return lambda *a, **k: mod._validate(mod.merge_model_config(mod._read_config(mod.STRENGTHS_FILE), _project(raw).model_config()), where="test") if k.get("merge_project", True) else mod._validate(mod._read_config(mod.STRENGTHS_FILE))


def test_telemetry_carries_strength_source_project_for_a_redefined_tier_and_plugin_otherwise(monkeypatch):
    monkeypatch.setattr(mod, "load_config", _patched({"strengths": {"mid": {"provider": "anthropic", "model": "claude-sonnet-5", "thinking": "high"}},
                              "activities": {"spec.survey": {"max_budget_usd": 2.0}}}))
    r = mod.resolve("analyst-survey", activity="spec.survey")
    assert r.strength == "mid" and r.strength_source == "project" and r.max_budget_usd == 2.0
    assert r.redacted()["strength_source"] == "project"
    assert "(patched by project)" in str(r)
    r2 = mod.resolve("verifier", activity="verify.impl")
    assert r2.strength_source == "plugin" and "(redefined" not in str(r2)


def test_the_dispatch_record_carries_the_source(monkeypatch):
    from models.dispatch import dispatch

    monkeypatch.setattr("models.dispatch.require_sandbox", lambda: None)
    monkeypatch.setattr(mod, "load_config", _patched({"strengths": {"strong": {"provider": "anthropic", "model": "claude-opus-5"}}}))
    payload = {"subtype": "success", "is_error": False, "result": "PASS", "total_cost_usd": 0.1, "num_turns": 1,
               "duration_ms": 1000, "session_id": "s", "usage": {"input_tokens": 1, "output_tokens": 1}, "permission_denials": []}
    out = dispatch("verifier", "judge", activity="verify.impl", runner=lambda r, p, **kw: payload)
    assert out.telemetry(task="T-1")["strength_source"] == "project"


def test_the_cost_report_never_shares_a_row_between_a_project_tier_and_the_plugins():
    from models.report import summarise

    events = [
        {"agent": "verifier", "strength": "strong", "provider": "anthropic", "strength_source": "plugin", "cost_usd": 1.0, "turns": 5, "ok": True},
        {"agent": "verifier", "strength": "strong", "provider": "anthropic", "strength_source": "project", "cost_usd": 0.2, "turns": 5, "ok": True},
        {"agent": "verifier", "strength": "strong", "provider": "anthropic", "cost_usd": 1.0, "turns": 5, "ok": True},  # an event from before the field
    ]
    rows = summarise(events)
    by = {(r["strength_source"]): r for r in rows}
    assert by["plugin"]["n"] == 2 and by["project"]["n"] == 1, "an old event with no field is the plugin's"


def test_the_ab_report_flags_an_arm_that_mixes_strength_sources():
    from models.ab_report import summarise

    rows = [{"_run": "1", "_sha": "abc", "agent": "verifier", "cost_usd": 1.0, "strength_source": "plugin"},
            {"_run": "2", "_sha": "abc", "agent": "verifier", "cost_usd": 1.0, "strength_source": "project"}]
    s = summarise({"on": rows})["on"]
    assert s["strength_sources"] == ["plugin", "project"]
    s = summarise({"on": rows[:1]})["on"]
    assert s["strength_sources"] == ["plugin"]


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

    t = dispatch("analyst-survey", "x", activity="spec.survey", runner=lambda r, p, **kw: payload).telemetry(task="T-1")
    assert t["cost_source"] == "sdk" and t["cost_usd"] == 4.2, "Anthropic: the vendor's own accounting"

    monkeypatch.setattr(mod, "load_config", _patched({"providers": {"deepseek": {"models": {"deepseek-v4-pro": {"price": {"input_per_mtok": 1.32, "output_per_mtok": 3.96,
                                                                                    "off_peak_multiplier": 0.5, "peak_utc": ["01:00-04:00"]}}}}}, "strengths": {"mid": {"provider": "deepseek", "model": "deepseek-v4-pro"}}}))
    t = dispatch("analyst-survey", "x", activity="spec.survey", runner=lambda r, p, **kw: payload).telemetry(task="T-1")
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
        {"agent": "verifier", "strength": "strong", "provider": "anthropic", "billing": "subscription",
         "cost_source": "sdk", "cost_usd": 2.0, "turns": 5, "ok": True},
        {"agent": "fullstack-engineer", "strength": "mid", "provider": "deepseek", "billing": "metered",
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

    assert resolve("verifier", activity="verify.impl").billing == "metered", "the conservative default: real money"
    with pytest.raises(ProjectError, match=re.escape("providers.deepseek.billing: must be 'metered' or 'subscription'")):
        _project({"providers": {"deepseek": {"billing": "free"}}}).model_config()
    ok = _project({"providers": {"anthropic": {"billing": "subscription"}}}).model_config()
    assert ok["providers"]["anthropic"]["billing"] == "subscription"


# --- the ceiling, enforced against the same number the record shows --------------------------


def test_the_meter_adds_usage_as_it_streams_and_fires_only_once_past_the_ceiling():
    """`--max-budget-usd` is checked by the CLI against its own table. Measured on
    DeepSeek, that table priced a $3.00 ceiling to bite at roughly $0.40 of real spend —
    a worker cut off a fifth of the way into its task, looking like the model failing."""
    from models import pricing

    price = {"input_per_mtok": 1.0, "output_per_mtok": 1.0}
    m = pricing.Meter(price, ceiling=1.0)
    assert m.over() is None, "nothing streamed yet is not over budget"
    m.add({"input_tokens": 400_000, "output_tokens": 0})
    assert m.over() is None and m.spent()[0] == 0.4
    m.add({"input_tokens": 400_000, "output_tokens": 0})
    assert m.over() is None, "$0.80 is under a $1.00 ceiling"
    m.add({"input_tokens": 300_000, "output_tokens": 0})
    usd, how = m.over()
    assert usd == 1.1 and "Mtok" in how, "the turn that crossed it reports the real total"
    assert m.turns_metered == 3


def test_the_meter_reads_both_usage_spellings_and_counts_nothing_it_was_not_given():
    """The API reports cache counts as `cache_read_input_tokens`, the SDK's result message
    without the `input`. A provider that reports no usage at all must read as UNENFORCED,
    never as free — that is the whole failure this path exists to end."""
    from models import pricing

    price = {"input_per_mtok": 1.0, "output_per_mtok": 1.0, "cache_read_per_mtok": 0.1}
    m = pricing.Meter(price, ceiling=10.0)
    m.add({"cache_read_input_tokens": 1_000_000})
    m.add({"cache_read_tokens": 1_000_000})
    assert m.totals["cache_read_tokens"] == 2_000_000 and m.spent()[0] == 0.2

    blind = pricing.Meter(price, ceiling=0.000001)
    blind.add(None)
    blind.add({})
    assert blind.turns_metered == 0 and blind.over() is None, "no usage is not zero usage"


def test_a_priced_dispatch_is_stopped_by_the_harness_at_its_real_ceiling(monkeypatch):
    """The real seam: the stream is read, the usage is added up at the tier's own rates,
    and the dispatch is cut off — in the same shape the CLI's own kill produces, so every
    caller that routes a budget kill keeps working without knowing who stopped it."""
    from claude_agent_sdk import AssistantMessage, TextBlock

    from models import dispatch as D
    from models.resolve import resolve

    closed: list[str] = []

    class _Stream:
        """An async generator the loop can abandon — `aclose` is what tears the CLI down."""

        def __init__(self):
            self._turns = iter(range(10))

        def __aiter__(self):
            return self

        async def __anext__(self):
            next(self._turns)
            return AssistantMessage(
                content=[TextBlock(text="reading")], model="deepseek-v4-pro",
                usage={"input_tokens": 1_000_000}, session_id="s-9",
            )

        async def aclose(self):
            closed.append("closed")

    monkeypatch.setattr(mod, "load_config", _patched({"providers": {"deepseek": {"models": {"deepseek-v4-pro": {"price": {"input_per_mtok": 1.0, "output_per_mtok": 1.0}}}}}, "strengths": {"mid": {"provider": "deepseek", "model": "deepseek-v4-pro", "thinking": "high"}},
        "activities": {"spec.survey": {"max_budget_usd": 3.0}}}))
    monkeypatch.setattr("claude_agent_sdk.query", lambda prompt, options: _Stream())
    monkeypatch.setattr(D, "broker", lambda *a, **k: None)
    monkeypatch.setattr(D, "require_sandbox", lambda: None)
    monkeypatch.setattr(D, "record", lambda *a, **k: True)

    r = resolve("analyst-survey", activity="spec.survey")
    assert r.max_budget_usd == 3.0
    payload = D._run_sdk(r, "prompt", cwd=".", env={}, timeout=30)

    assert payload["subtype"] == "error_max_budget_usd" and payload["is_error"]
    assert payload["num_turns"] == 3, "stopped on the turn that crossed $3.00, not after ten"
    assert payload["usage"]["input_tokens"] == 3_000_000
    assert payload["ceiling_enforced_by"] == "harness"
    assert "$3.0000 of real spend passed its $3.00 ceiling" in payload["result"]
    assert closed == ["closed"], "the stream is closed — abandoning it would keep spending"

    out = D.dispatch("analyst-survey", "x", activity="spec.survey", runner=lambda *a, **k: payload)
    assert out.terminal == "budget" and out.budget_exhausted
    assert out.priced_cost()[0] == 3.0, "the ceiling and the record are the same number"
    assert out.telemetry(task="T-1")["ceiling_source"] == "harness"


def test_the_cli_enforces_an_anthropic_ceiling_and_is_given_none_for_a_priced_tier():
    """ONE ENFORCER, IN KNOWN UNITS. The CLI checks `max_budget_usd` against its own price
    table, which for a model it does not know is a fiction — measured twice against
    DeepSeek off-peak at 10.3x and 9.7x the real cost. Its kill therefore lands at a
    real-dollar figure nobody can state, which is not a bound. This was passed a loosened
    multiple for a while (10x, then 25x); at 10x it was close enough to race the meter it
    was meant to back up, and the fix for that was never a bigger number — it was noticing
    that a threshold in an unknown currency is not a safety property."""
    from models.resolve import resolve

    # Anthropic: the SDK's figure is the vendor's own accounting, so the CLI enforces.
    assert resolve("analyst-survey", activity="spec.survey").sdk_options(cwd=".").max_budget_usd == 3.0
    r = resolve("analyst-survey", activity="spec.survey")
    object.__setattr__(r, "price", {"input_per_mtok": 1.0, "output_per_mtok": 1.0})
    assert r.sdk_options(cwd=".").max_budget_usd is None, "a priced tier is metered here, not there"


def test_a_priced_tier_that_streams_no_usage_is_stopped_rather_than_run_uncapped(monkeypatch):
    """What the CLI backstop was really for. A priced tier is handed no CLI ceiling, so the
    meter is the only enforcer — and a meter with nothing to add up enforces nothing.
    `probe-compat.sh` certifies streamed token accounting before a provider is routed; this
    is the same failure appearing mid-flight, and stopping beats an uncapped dispatch that
    looks capped."""
    from claude_agent_sdk import AssistantMessage, TextBlock

    from models import dispatch as D
    from models.resolve import UNMETERED_TURNS_ALLOWED, resolve

    class _Mute:
        """A provider that answers but reports no usage at all."""

        def __init__(self):
            self._n = iter(range(20))

        def __aiter__(self):
            return self

        async def __anext__(self):
            n = next(self._n)
            return AssistantMessage(content=[TextBlock(text="working")], model="m",
                                    usage=None, message_id=f"m{n}")

        async def aclose(self):
            pass

    monkeypatch.setattr(mod, "load_config", _patched({"providers": {"deepseek": {"models": {"deepseek-v4-pro": {"price": {"input_per_mtok": 1.0, "output_per_mtok": 1.0}}}}}, "strengths": {"mid": {"provider": "deepseek", "model": "deepseek-v4-pro", "thinking": "high"}},
        "activities": {"spec.survey": {"max_budget_usd": 3.0}}}))
    monkeypatch.setattr("claude_agent_sdk.query", lambda prompt, options: _Mute())
    monkeypatch.setattr(D, "broker", lambda *a, **k: None)

    payload = D._run_sdk(resolve("analyst-survey", activity="spec.survey"), "p", cwd=".", env={}, timeout=30)
    assert payload["subtype"] == "error_unenforceable_ceiling"
    assert payload["num_turns"] == UNMETERED_TURNS_ALLOWED, "stopped as soon as it was sure"
    assert "nothing was checking its $3.00 ceiling" in payload["result"]

    out = D.dispatch("analyst-survey", "x", activity="spec.survey", runner=lambda *a, **k: payload)
    assert out.terminal == "unenforceable_ceiling" and not out.budget_exhausted, \
        "nothing was exceeded — this is a configuration fault, not a task to split"
    assert out.ceiling_source == "none"


def test_a_priced_tier_whose_provider_reports_no_usage_records_an_unenforced_ceiling(monkeypatch, capsys, tmp_path):
    """The case that must never pass silently: the CLI's ceiling was deliberately loosened
    for this tier, and nothing took its place."""
    from models import dispatch as D

    monkeypatch.setattr(mod, "load_config", _patched({"providers": {"deepseek": {"models": {"deepseek-v4-pro": {"price": {"input_per_mtok": 1.0, "output_per_mtok": 1.0}}}}}, "strengths": {"mid": {"provider": "deepseek", "model": "deepseek-v4-pro", "thinking": "high"}},
        "activities": {"spec.survey": {"max_budget_usd": 3.0}}}))
    monkeypatch.setattr(D, "require_sandbox", lambda: None)
    monkeypatch.setattr(D, "record", lambda *a, **k: True)
    monkeypatch.setattr(D, "RESULT_DIR", tmp_path / "out")
    payload = {"subtype": "success", "is_error": False, "result": "ok", "total_cost_usd": 0.0,
               "num_turns": 4, "duration_ms": 10, "session_id": "s", "usage": {}, "permission_denials": [],
               "metered_turns": 0, "ceiling_enforced_by": "none"}

    out = D.dispatch("analyst-survey", "x", activity="spec.survey", runner=lambda *a, **k: payload)
    assert out.ceiling_source == "none"
    assert out.telemetry(task="T-1")["ceiling_source"] == "none"

    pf = tmp_path / "p.txt"
    pf.write_text("do the thing")
    monkeypatch.setattr(D, "_run_sdk", lambda *a, **k: payload)
    D.main(["analyst-survey", "--activity", "spec.survey", "--prompt-file", str(pf), "--task", "T-1"])
    assert "CEILING NOT ENFORCED" in capsys.readouterr().err


def test_one_api_response_is_metered_and_counted_once_however_many_blocks_it_arrives_in():
    """MEASURED on a live DeepSeek stream (2026-09-23): each response arrives as one
    `AssistantMessage` per content block — a thinking block, then a tool-use block — every
    one carrying the SAME usage and the same `message_id`. Ten messages for what the result
    message counted as five turns. Summing them as they arrive doubles both the cost and
    the turn count, so the ceiling fires at half the spend it names."""
    from models import pricing

    price = {"input_per_mtok": 1.32, "output_per_mtok": 3.96, "cache_read_per_mtok": 0.044}
    m = pricing.Meter(price, ceiling=99.0)
    # The exact stream that was measured: (message_id, input, cache_read), each twice.
    for mid, fresh, read in (("a", 12769, 0), ("b", 197, 12928), ("c", 160, 13184),
                             ("d", 104, 13440), ("e", 148, 13568)):
        for _ in range(2):
            m.add({"input_tokens": fresh, "cache_read_input_tokens": read, "output_tokens": 0}, mid)
    assert m.totals["input_tokens"] == 13378, "the result message's own input total"
    assert m.totals["cache_read_tokens"] == 53120, "the result message's own cache-read total"
    assert m.turns_metered == 5, "five API responses, not ten messages"
    # Prompt tokens only — the streamed usage reports output as 0 and the real 682 arrives
    # in a result message a killed dispatch never gets. Named in the derivation, not padded.
    assert "prompt only" in m.spent()[1]


def test_a_metered_kill_reports_the_cache_it_actually_read():
    """The meter records canonical key names, the SDK's result message the `_input_` ones.
    Reading only the SDK's spelling reported a metered kill as 0% cache hit on a worker
    that had read 53,120 tokens from cache — a lie about the one number the cost analysis
    had to reconstruct by hand."""
    from models import dispatch as D

    payload = {"subtype": "error_max_budget_usd", "is_error": True, "result": "stopped",
               "total_cost_usd": 0.0, "num_turns": 5, "duration_ms": 10, "session_id": "s",
               "usage": {"input_tokens": 13378, "cache_read_tokens": 53120, "output_tokens": 0},
               "permission_denials": [], "ceiling_enforced_by": "harness", "metered_turns": 5}
    out = D.dispatch("analyst-survey", "x", activity="spec.survey", runner=lambda *a, **k: payload)
    assert out.cache_read_tokens == 53120 and out.prompt_tokens == 66498
    assert out.cache_hit_pct == 79.9


def test_prompt_only_labels_a_kill_and_never_a_dispatch_that_finished(monkeypatch):
    """Caught by running it for real (2026-09-23): a metered dispatch that COMPLETED was
    labelled `priced (prompt only, …)` although its $0.015494 included 1,844 output tokens
    at $1.98/Mtok. Metering is how the ceiling was watched; it says nothing about whether
    the final usage was complete."""
    from models import dispatch as D

    monkeypatch.setattr(mod, "load_config", _patched({"providers": {"deepseek": {"models": {"deepseek-v4-pro": {"price": {"input_per_mtok": 0.66, "output_per_mtok": 1.98, "cache_read_per_mtok": 0.022}}}}}, "strengths": {"mid": {"provider": "deepseek", "model": "deepseek-v4-pro", "thinking": "high"}},
        "activities": {"spec.survey": {"max_budget_usd": 3.0}}}))
    full = {"subtype": "success", "is_error": False, "result": "done", "total_cost_usd": 0.16,
            "num_turns": 7, "duration_ms": 10, "session_id": "s", "permission_denials": [],
            "usage": {"input_tokens": 15524, "output_tokens": 1844, "cache_read_input_tokens": 72576},
            "ceiling_enforced_by": "harness", "metered_turns": 7}
    out = D.dispatch("analyst-survey", "x", activity="spec.survey", runner=lambda *a, **k: full)
    usd, how = out.priced_cost()
    assert usd == 0.015494, "input + output + cache read, at the tier's own rates"
    assert "prompt only" not in how and out.ceiling_source == "harness"

    killed = {**full, "subtype": "error_max_budget_usd", "is_error": True,
              "usage": {"input_tokens": 15524, "cache_read_tokens": 72576, "output_tokens": 0}}
    out = D.dispatch("analyst-survey", "x", activity="spec.survey", runner=lambda *a, **k: killed)
    assert "prompt only" in out.priced_cost()[1], "a kill never saw its output count"


def test_two_strengths_differing_only_by_thinking_off_anthropic_are_warned_about():
    """`strong` and `strategic` are the SAME model and differ only in `effort` — which is
    what makes the top of the ladder mean anything. Routed at a provider that ignores the
    parameter, the rung is a no-op: a stage that escalated because it needed deeper
    deliberation is re-run with exactly what it had, at the same price, reporting success,
    and nothing else would notice.

    MEASURED (2026-09-24), five runs per level against deepseek-v4-pro on one prompt: output
    tokens by effort were low 181, high 178, max 215 (medians), every pair's spread
    overlapping and `thinking_tokens` 0 throughout. The same probe on claude-opus-5[1m]
    moved 156 -> 384 -> 583 output and 39 -> 113 -> 299 thinking, so the probe sees the
    effect where there is one. One provider, one prompt — a warning, not a refusal."""
    from models.check_project import _collapsed_strengths

    anthropic = {
        "strengths": {
            "mid": {"provider": "anthropic", "model": "claude-sonnet-5", "thinking": "high"},
            "strong": {"provider": "anthropic", "model": "claude-opus-5[1m]", "thinking": "xhigh"},
            "elite": {"provider": "anthropic", "model": "claude-opus-5[1m]", "thinking": "max"},
        },
        "activities": {},
    }
    assert _collapsed_strengths(anthropic) == [], "thinking IS the step up on Anthropic — never warn there"

    collapsed = {
        "strengths": {
            "warm": {"provider": "deepseek", "model": "deepseek-v4-pro", "thinking": "xhigh"},
            "hot": {"provider": "deepseek", "model": "deepseek-v4-pro", "thinking": "max"},
        },
        "activities": {"verify.impl": {"agent": "lens", "strengths": ["warm", "hot"]}},
    }
    [warning] = _collapsed_strengths(collapsed)
    assert "differ only by `thinking`" in warning and "max vs xhigh" in warning or "xhigh vs max" in warning
    assert "drop it" in warning, "a warning that does not say what to do is noise"
    assert "verify.impl" in warning, "and it names the chain that holds both"

    # ALL PAIRS, not ladder adjacency: the ladder is gone, and two strengths are a
    # distinction-without-a-difference wherever they sit relative to each other.
    assert _collapsed_strengths({**collapsed, "activities": {}}), "still a defect with no chain using them"

    # Escaped by giving one a different model, which is the documented fix.
    fixed = {**collapsed, "strengths": {**collapsed["strengths"],
             "hot": {"provider": "deepseek", "model": "deepseek-r2", "thinking": "max"}}}
    assert _collapsed_strengths(fixed) == []
    # And two strengths at the same model AND the same thinking are not this defect.
    same = {**collapsed, "strengths": {**collapsed["strengths"],
            "hot": {"provider": "deepseek", "model": "deepseek-v4-pro", "thinking": "xhigh"}}}
    assert _collapsed_strengths(same) == []


def test_two_strengths_on_one_model_state_its_rate_once(agents):
    """THE DEFECT THE 0.11.0 MOVE FIXED, still pinned. `strong` and `elite` are the SAME
    model and differ only in thinking, so routing both at a third-party provider would mean
    writing one rate twice with nothing comparing them — and two could declare DIFFERENT
    rates for one model and both validate, leaving every cost record and every ceiling from
    one of them wrong with no symptom. A rate is a fact about a model at a provider."""
    cfg = merged({
        "providers": {"deepseek": {
            "env": {"ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
                    "ANTHROPIC_AUTH_TOKEN": "${DEEPSEEK_API_KEY}"},
            "models": {"deepseek-v4-pro": {"price": {"input_per_mtok": 1.32, "output_per_mtok": 3.96}}},
        }},
        # Two strengths, one model — the rate stated once above.
        "strengths": {"strong": {"provider": "deepseek", "model": "deepseek-v4-pro", "thinking": "xhigh"},
                      "elite": {"provider": "deepseek", "model": "deepseek-v4-pro", "thinking": "max"}},
    })
    a = mod.resolve("lens", activity="verify.impl", config=cfg, agents_dir=agents)
    b = mod.resolve("designer", activity="design.create", complexity="complex", config=cfg, agents_dir=agents)
    assert (a.strength, b.strength) == ("strong", "elite"), "two different strengths"
    assert a.model == b.model == "deepseek-v4-pro"
    assert a.price == b.price == {"input_per_mtok": 1.32, "output_per_mtok": 3.96}, \
        "one model, one rate — there is no second place for them to disagree"


def test_a_projects_model_rates_patch_per_model_and_per_key(agents):
    """A project correcting one rate must not drop the others declared beside it — the same
    rule as a stack's `commands`, for the same reason."""
    plugin = {
        "strengths": {"mid": {"provider": "acme", "model": "m1", "thinking": "high"}},
        "activities": {"work.implement": {"agent": "grunt", "strengths": ["mid"], "max_budget_usd": 1.0}},
        "providers": {"acme": {"env": {}, "models": {
            "m1": {"price": {"input_per_mtok": 1.0, "output_per_mtok": 2.0, "cache_read_per_mtok": 0.1}},
            "m2": {"price": {"input_per_mtok": 9.0, "output_per_mtok": 9.0}},
        }}},
    }
    out = mod.merge_model_config(plugin, {"providers": {"acme": {"models": {"m1": {"price": {
        "input_per_mtok": 1.5, "output_per_mtok": 2.0, "cache_read_per_mtok": 0.1}}}}}})
    models = out["providers"]["acme"]["models"]
    assert models["m1"]["price"]["input_per_mtok"] == 1.5, "the corrected rate"
    assert models["m2"]["price"]["input_per_mtok"] == 9.0, "the sibling model survives"
    assert out["providers"]["acme"]["env"] == {}, "env is untouched by a models patch"


def test_the_templates_model_config_example_is_a_valid_config():
    """The template is what a project COPIES, so an example that would be refused is worse
    than no example. Measured: the shipped one routed `worker` at openrouter with no rates
    at all — a config the validator refuses by name — and nothing noticed, because a
    commented block is never loaded.

    Lifted from between the sentinels, uncommented, and put through the real project schema
    and the real merge, so the example cannot drift from what the code accepts."""
    import re

    import yaml

    from models.resolve import PLUGIN_ROOT

    template = (PLUGIN_ROOT / "templates" / "harness.yaml.example").read_text()
    block = re.search(r"# EXAMPLE-BEGIN model-config.*?\n(.*?)# EXAMPLE-END", template, re.DOTALL)
    assert block, "the model-config example lost its sentinels"
    raw = "\n".join(ln[1:] if ln.startswith("#") else ln for ln in block.group(1).splitlines())
    example = yaml.safe_load(raw)
    assert set(example) == {"strengths", "activities", "providers"}, f"unexpected keys: {sorted(example)}"

    # Against the REAL plugin config, not this file's fixture: the template is a patch over
    # what the plugin ships, so that is the only merge that proves it loads.
    cfg = mod._validate(
        mod.merge_model_config(mod._read_config(mod.STRENGTHS_FILE), _project(example).model_config()),
        where="template",
    )
    assert cfg["strengths"]["cheap"]["provider"] == "openrouter"
    price = cfg["providers"]["openrouter"]["models"]["qwen/qwen3-coder-plus"]["price"]
    assert price["input_per_mtok"] == 1.00 and price["output_per_mtok"] == 5.00
    # The half the example exists to demonstrate: a strength off Anthropic reaches a priced
    # model, and an activity routes work at it.
    assert cfg["strengths"]["cheap"]["model"] in cfg["providers"]["openrouter"]["models"]
    assert "cheap" in cfg["activities"]["work.implement"]["strengths"]
