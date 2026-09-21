"""Cost levers — each a switch with a measured default, read in exactly one place.

A field cost analysis ranked the levers and then said the only honest thing: change one
at a time, under measurement, because harness variance is model-sized and token cost on
identical work varies up to 30x. So every lever here is OFF until the lab has sized it,
each is flippable without a plugin release, and every dispatch records which were on —
otherwise a series with a lever half-applied cannot be read at all.

Precedence: the environment (`MAD_HARNESS_<LEVER>`, what the A/B rig sets per arm), then
the project's `harness.yaml` `dispatch:` block (what a consuming repository sets once it
has decided), then the default.

    lever            env                        harness.yaml            default
    cache_ttl        MAD_HARNESS_CACHE_TTL      dispatch.cache_ttl      unset (the CLI's rule)
    static_prefix    MAD_HARNESS_STATIC_PREFIX  dispatch.static_prefix  false
    stagger_seconds  MAD_HARNESS_STAGGER_SECONDS dispatch.stagger_seconds 0
    task_budget      MAD_HARNESS_TASK_BUDGET_TOKENS  dispatch.task_budget_tokens, else tiers.yaml <tier>.task_budget_tokens
    preload          MAD_HARNESS_PRELOAD        —  (agents/<name>.md skills:)  none
    lean_catalog     MAD_HARNESS_LEAN_CATALOG   dispatch.lean_catalog   true  (the exception — see below)
    plan_tiers       MAD_HARNESS_PLAN_TIERS     dispatch.plan_tiers     true — §3 at strong or lower with escalation (the owner's decision; see the default below)
    experiment       MAD_HARNESS_EXPERIMENT     —                       — (a label, recorded)

DOCTRINE IS NOT A LEVER. The skills an agent declares in its frontmatter are part of its
system prompt on every dispatch — `resolve.doctrine()`, delivered through the SDK's
system-prompt append, refused if a declared skill is missing — because the CLI does not
preload them on the `--agent` path (measured 0.10.8) and a switch that could turn them
off was measured turning them off: "cheaper" meant "did less" (0.10.17). `preload` is
the rig's arm for measuring an ADDITIONAL skill before an agent declares it, delivered
the same way so the arm measures exactly what declaring would do. `task_budget` has a
tier default the project may override in `harness.yaml` — a project whose tasks carry
34KB records needs more room than the lab's.

THE ONE LEVER THAT DEFAULTS ON is `lean_catalog`, and it is the exception to "off until
the lab has sized it" because it removes rather than changes: the Skill catalog a worker
sees drops 17 bundled CLI skills and the plugin's 10 orchestrator commands, which a
headless worker can neither use nor must ever run. Sized at the request level, where the
effect is deterministic — 26,130 → 22,743 tokens on a worker's first request — and below
run-to-run variance by construction, which is exactly the case a run-level A/B cannot
read. `ab.sh lean_catalog` exists so it can be sized that way anyway.
"""

from __future__ import annotations

import os
from typing import Any

CACHE_TTLS = ("5m", "1h")

_ENV = {
    "cache_ttl": "MAD_HARNESS_CACHE_TTL",
    "static_prefix": "MAD_HARNESS_STATIC_PREFIX",
    "stagger_seconds": "MAD_HARNESS_STAGGER_SECONDS",
    "task_budget": "MAD_HARNESS_TASK_BUDGET_TOKENS",
    "preload": "MAD_HARNESS_PRELOAD",
    "lean_catalog": "MAD_HARNESS_LEAN_CATALOG",
    "plan_tiers": "MAD_HARNESS_PLAN_TIERS",
}
_DEFAULT: dict[str, Any] = {
    "cache_ttl": None, "static_prefix": False, "stagger_seconds": 0, "task_budget": None, "preload": (),
    # ON BY DEFAULT — the one lever that removes rather than changes. Measured at the
    # request level (0.10.8): a worker's first prompt fell 26,130 -> 22,743 tokens when
    # its Skill catalog held the plugin's own skills instead of those plus 17 bundled
    # CLI skills and 10 orchestrator commands a headless worker can never use.
    "lean_catalog": True,
    # §3 AT STRONG OR LOWER, WITH ESCALATION — ON, by the owner's decision (2026-09-21),
    # not by a separated spread: "I agree to downgrading the pre steps to strong or lower,
    # with escalation." Measured first (the release A/B): planning a 3-task epic that
    # already had its tasks took 29 minutes and $7.33, the architect at strategic (Opus,
    # max effort) ~4 minutes a dispatch, against $1.07 and 3.5 minutes to build it; and
    # the first plan-only series put the sanity-check at strong vs strategic at −27% cost,
    # −28% time, −42% output (n=2). The exception to "measured before moved" is stated
    # here because it is one: the default moved on a decision, and the series that sizes
    # the whole rule (planner and audit included) is in docs/upgrading.md.
    "plan_tiers": True,
}
#: The harness.yaml key each lever reads, where it differs from the lever's name.
_KEY = {"task_budget": "task_budget_tokens"}
_TRUE = ("1", "true", "yes", "on")


def _project_block() -> dict[str, Any]:
    try:
        from .project import load

        return load().dispatch()
    except Exception:  # noqa: BLE001 — no config is "defaults", never a crash in a lever read
        return {}


def lever(name: str, block: dict[str, Any] | None = None) -> Any:
    """The current value of one lever, by precedence. `block` injects the project block
    for tests; None reads harness.yaml."""
    raw = os.environ.get(_ENV[name])
    if raw is None or raw == "":
        block = _project_block() if block is None else block
        key = _KEY.get(name, name)
        return block[key] if key in block else _DEFAULT[name]
    if name in ("static_prefix", "lean_catalog", "plan_tiers"):
        return raw.strip().lower() in _TRUE
    if name == "stagger_seconds":
        return max(0, int(raw))
    if name == "task_budget":
        return int(raw) or None
    if name == "preload":
        return tuple(x.strip() for x in raw.split(",") if x.strip())
    if name == "cache_ttl":
        if raw not in CACHE_TTLS:
            raise ValueError(f"{_ENV[name]} must be one of {', '.join(CACHE_TTLS)}, not {raw!r}")
        return raw
    return raw


def experiment() -> str | None:
    """A label the A/B rig sets per arm and run, recorded on every dispatch event so the
    series can be grouped without guessing from timestamps."""
    return os.environ.get("MAD_HARNESS_EXPERIMENT") or None


def snapshot(block: dict[str, Any] | None = None) -> dict[str, Any]:
    """What was on for this dispatch — recorded with it."""
    return {n: lever(n, block) for n in _ENV}
