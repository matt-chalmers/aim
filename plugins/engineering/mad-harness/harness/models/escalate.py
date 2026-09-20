"""When a failed dispatch should be retried on a stronger tier — and when it must not.

ESCALATION IS EXPENSIVE, and this module exists mainly to stop it happening
casually. Two measured facts from this repo set the policy:

  * ``tokens ~= 18,700 + 2,600 x tool_calls`` (swarm.md, 24 lens dispatches).
    The per-dispatch fixed base dominates.
  * A trivial one-turn dispatch with no tools measured $0.034.

So a retry does not cost "a bit more" — it re-pays the whole base, and an
escalation additionally carries the prior attempt's context, making the second
dispatch strictly larger than the first. Escalating a task that a worker would
have fixed on its own second try is a pure loss. Escalating one that no worker
will ever get right is the only case that pays.

Hence the split the proposal asks for, with the bar set high:

    implementation failure   the worker owns it. Red test, compile error, lint.
                             Retry on the SAME tier or just let the worker fix it.
    reasoning failure        a stronger model is the only thing that changes the
                             outcome. Escalate, carrying everything already tried.

A budget exhaustion is deliberately NOT an escalation trigger. It means the work
was bigger than the ceiling, and the honest responses are to raise the ceiling or
split the task — silently re-running it on a costlier tier turns a visible limit
into a larger bill.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .dispatch import Outcome
from .resolve import ConfigError, load_config
from .verdict import return_status

#: A worker may ask for help explicitly. Cheapest possible signal, and the only
#: one that does not require guessing from prose.
ESCALATE_MARKER = re.compile(r"^\s*ESCALATE\s*:", re.MULTILINE | re.IGNORECASE)

#: Return-contract outcomes that mean "I cannot proceed", as distinct from
#: "I tried and the tests are red".
BLOCKED_VERDICTS = ("BLOCKED", "NEEDS-SERIAL-LANE")

#: Phrases that indicate the worker hit a *reasoning* wall rather than a bug.
#: Deliberately narrow: a broad list would catch ordinary hedging and escalate
#: everything, which is the failure mode this module exists to prevent.
REASONING_SIGNALS = (
    "contradicts the approved",
    "contradicts the adr",
    "cannot determine the correct",
    "cannot determine correct",
    "requirements are ambiguous",
    "ambiguous requirement",
    "outside its original scope",
    "outside my original scope",
    "propose changing the architecture",
    "architecture change",
)


@dataclass(frozen=True)
class Classification:
    category: str
    retryable: bool
    escalate: bool
    why: str


def classify(
    outcome: Outcome, *, attempt: int = 1, previous_failure: str | None = None
) -> Classification:
    """Decide what kind of failure this was, and therefore what to do about it."""
    if outcome.ok:
        return Classification("success", False, False, "dispatch succeeded")

    subtype = str(outcome.raw.get("subtype") or "")
    if "budget" in subtype:
        return Classification(
            "budget_exhausted",
            False,
            False,
            "the work exceeded its ceiling. Raise max_budget_usd for this tier or "
            "split the task — escalating turns a visible limit into a larger bill.",
        )

    text = outcome.text or ""

    if ESCALATE_MARKER.search(text):
        return Classification(
            "reasoning_failure",
            False,
            True,
            "the worker asked for escalation explicitly",
        )

    # The return contract puts the id FIRST (`<id> · PASS · …`); this used to take the
    # first `·`-token as the verdict and so never recognised a BLOCKED return. One parser.
    verdict = return_status(text)
    if verdict in BLOCKED_VERDICTS:
        return Classification(
            "blocked",
            False,
            True,
            f"the worker returned {verdict} — it cannot proceed at this tier",
        )

    lowered = text.lower()
    for signal in REASONING_SIGNALS:
        if signal in lowered:
            return Classification(
                "reasoning_failure",
                False,
                True,
                f"the worker reported a reasoning wall: {signal!r}",
            )

    # Repetition is the only *inferred* escalation trigger, and it needs two
    # attempts to have failed the same way. One red test is a worker's own job.
    if attempt >= 2 and previous_failure and _same_root(previous_failure, text):
        return Classification(
            "reasoning_failure",
            False,
            True,
            f"attempt {attempt} failed the same way as the last one",
        )

    return Classification(
        "implementation_error",
        True,
        False,
        "an ordinary failure the worker should fix itself; escalating here re-pays "
        "the whole fixed dispatch base for nothing",
    )


def _same_root(a: str, b: str) -> bool:
    """Whether two failure reports look like the same underlying problem.

    Compares the set of quoted identifiers and test names rather than the prose,
    because a model rewords its explanation between attempts while the failing
    test name stays put.
    """

    def tokens(s: str) -> set[str]:
        return set(re.findall(r"\b(?:test_\w+|[A-Za-z_]+Error|[\w/]+\.\w{2,4})\b", s))

    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.5


def next_tier(current: str, config: dict[str, Any] | None = None) -> str | None:
    """The next rung up, or None at the top."""
    config = config or load_config()
    ladder = config.get("ladder")
    if not ladder:
        raise ConfigError(
            "tiers.yaml defines no `ladder`, so escalation has no direction"
        )
    if current not in ladder:
        raise ConfigError(f"tier {current!r} is not on the ladder {ladder}")
    i = ladder.index(current)
    return ladder[i + 1] if i + 1 < len(ladder) else None


def escalation_prompt(original: str, attempts: list[Outcome]) -> str:
    """Build the stronger tier's prompt, carrying everything already tried.

    Proposal §14, and the reason it matters here specifically: a fresh "try this
    task" throws away the one thing that justified the extra spend — knowing where
    the cheaper model actually broke. The stronger model should open already
    knowing what has been ruled out.
    """
    parts = [
        original,
        "",
        "---",
        "",
        "## Escalation context",
        "",
        f"This task has already been attempted {len(attempts)} time(s) on a "
        f"cheaper tier and failed. You are the escalation.",
        "",
    ]
    for i, a in enumerate(attempts, start=1):
        parts += [
            f"### Attempt {i} — {a.resolved.provider}/{a.resolved.model} "
            f"(tier {a.resolved.tier}), {a.turns} turns, ${a.cost_usd:.4f}",
            "",
            a.text.strip() or "<no output>",
            "",
        ]
    parts += [
        "Do not simply repeat the approach above. The reason this reached you is "
        "that repeating it is not expected to work. State explicitly what you are "
        "doing differently and why.",
    ]
    return "\n".join(parts)
