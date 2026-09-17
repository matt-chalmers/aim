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
    task_budget      MAD_HARNESS_TASK_BUDGET_TOKENS  tiers.yaml <tier>.task_budget_tokens  unset
    preload          MAD_HARNESS_PRELOAD        —  (agents/<name>.md skills:)  none
    experiment       MAD_HARNESS_EXPERIMENT     —                       — (a label, recorded)

`task_budget` and `preload` are env-only because in production they are a tier field and
an agent's frontmatter respectively; the env form exists so an A/B can flip them per arm
without editing either. `preload` names skills whose SKILL.md is appended to the prompt —
the same text a frontmatter preload puts in the system prompt, arriving one message later.
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
}
_DEFAULT: dict[str, Any] = {
    "cache_ttl": None, "static_prefix": False, "stagger_seconds": 0, "task_budget": None, "preload": (),
}
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
        return block.get(name, _DEFAULT[name]) if name in block else _DEFAULT[name]
    if name == "static_prefix":
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
