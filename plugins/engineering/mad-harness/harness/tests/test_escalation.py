"""Escalation policy: mostly a test that we DON'T escalate.

The expensive mistake is not failing to escalate — it is escalating a task the
worker would have fixed itself. A retry re-pays the whole ~18,700-token fixed
base, and an escalation additionally carries the prior attempt's context, so the
second dispatch is strictly larger than the first. Most of these tests therefore
assert that an ordinary failure stays put.
"""

from __future__ import annotations

import pytest

from models.dispatch import Outcome
from models.escalate import (
    classify,
    escalation_prompt,
    next_tier,
)
from models.resolve import ConfigError, Resolved


def an_outcome(text="", ok=False, subtype="success", tier="worker", **kw):
    return Outcome(
        resolved=Resolved(
            agent="fullstack-engineer",
            tier=tier,
            reason="agent default",
            provider="anthropic",
            model="sonnet",
            effort="high",
            max_budget_usd=1.5,
            env={},
            missing_env=(),
        ),
        ok=ok,
        text=text,
        cost_usd=kw.get("cost", 0.42),
        input_tokens=10,
        output_tokens=20,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        turns=kw.get("turns", 4),
        duration_ms=1000,
        session_id="s",
        permission_denials=[],
        raw={"subtype": subtype},
    )


# --- the default must be "do not escalate" ------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "FAIL · tests red · test_engine_applies_rule failed with AssertionError",
        "FAIL · ruff check reported 3 errors",
        "FAIL · TypeError in services/engine.py",
    ],
)
def test_an_ordinary_failure_does_not_escalate(text):
    """A red test is the worker's own job. Escalating here buys nothing and
    re-pays the entire fixed dispatch base."""
    c = classify(an_outcome(text))
    assert c.category == "implementation_error"
    assert c.retryable and not c.escalate


def test_success_never_escalates():
    assert not classify(an_outcome("PASS · one clean commit", ok=True)).escalate


def test_budget_exhaustion_does_not_escalate():
    """It means the work exceeded its ceiling, not that the model was too weak.

    Silently re-running on a costlier tier turns a visible limit into a larger
    bill — the opposite of what the ceiling is for.

    The subtype is the REAL one the CLI emits, measured. An earlier version of this
    test used ``error_max_budget``, which the CLI never returns; it passed anyway
    because classify() substring-matches "budget", making it a test that could not
    have failed.
    """
    c = classify(an_outcome("", subtype="error_max_budget_usd"))
    assert c.category == "budget_exhausted"
    assert not c.escalate and not c.retryable
    assert "ceiling" in c.why


# --- the cases that do escalate -----------------------------------------------


def test_an_explicit_request_escalates():
    c = classify(
        an_outcome("ESCALATE: this contradicts ADR-0027 and I cannot resolve it")
    )
    assert c.escalate and c.category == "reasoning_failure"


@pytest.mark.parametrize("verdict", ["BLOCKED", "NEEDS-SERIAL-LANE"])
def test_a_blocked_verdict_escalates(verdict):
    assert classify(
        an_outcome(f"{verdict} · cannot proceed · no files touched")
    ).escalate


@pytest.mark.parametrize(
    "phrase",
    [
        "the implementation contradicts the approved design",
        "I cannot determine the correct behaviour from the criteria",
        "the requirements are ambiguous on the edge case",
    ],
)
def test_a_reasoning_wall_escalates(phrase):
    c = classify(an_outcome(f"FAIL · {phrase}"))
    assert c.escalate and c.category == "reasoning_failure"


def test_repetition_escalates_only_from_the_second_attempt():
    """One red test is a worker's job; the same red test twice is a wall."""
    first = "FAIL · test_thing_rejects_after_cutoff failed with AssertionError"
    second = "FAIL · still failing: test_thing_rejects_after_cutoff, AssertionError"

    assert not classify(an_outcome(second), attempt=1, previous_failure=first).escalate
    assert classify(an_outcome(second), attempt=2, previous_failure=first).escalate


def test_a_different_failure_on_attempt_two_does_not_escalate():
    """Progress, even messy progress, is the worker doing its job."""
    first = "FAIL · test_thing_rejects_after_cutoff failed"
    second = "FAIL · test_report_paging failed, different area entirely"
    assert not classify(an_outcome(second), attempt=2, previous_failure=first).escalate


# --- ladder -------------------------------------------------------------------


def test_the_ladder_is_explicit_and_terminates():
    assert next_tier("worker") == "strong"
    assert next_tier("strong") == "strategic"
    assert next_tier("strategic") is None, "the top rung must not wrap around"


def test_an_off_ladder_tier_is_refused():
    with pytest.raises(ConfigError, match="not on the ladder"):
        next_tier("turbo")


def test_a_config_without_a_ladder_is_refused():
    with pytest.raises(ConfigError, match="no `ladder`"):
        next_tier("worker", config={"tiers": {"worker": {}}})


# --- context carrying ---------------------------------------------------------


def test_the_escalation_prompt_carries_what_was_already_tried():
    """A fresh "try this task" throws away the only thing that justified the spend."""
    attempts = [
        an_outcome("FAIL · test_x red · tried widening the join", cost=0.31, turns=9),
        an_outcome("FAIL · test_x still red · tried a subquery", cost=0.44, turns=12),
    ]
    p = escalation_prompt("Implement the cutoff rule.", attempts)

    assert "Implement the cutoff rule." in p, "the original task must survive"
    assert "tried widening the join" in p
    assert "tried a subquery" in p
    assert "anthropic/sonnet" in p and "$0.31" in p, "what it cost and on what"
    assert "Attempt 1" in p and "Attempt 2" in p
    assert "doing differently" in p, "must ask for a different approach, not a repeat"
