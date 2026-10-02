"""Fail loudly when the model routing config and the agent definitions disagree.

WHY THIS EXISTS. The strength lives in ``strengths.yaml``; each activity declares which
it wants; and each agent ALSO carries ``model:``/``effort:``, which is what Claude
Code reads when the agent is run natively (``claude --agent <name>``) rather than
through the boundary. Two readers, one intent. Let them drift and the same agent
runs a different model depending on how it was called — the worst kind of bug,
because both paths work and only the bill and the quality differ. (The Agent-tool
path is refused for this plugin's agents by ``swarm/guard-agent-tool.sh``; the
native CLI path is what the mirror still guards.)

A NON-ANTHROPIC TIER IS EXEMPT, out loud. The frontmatter has no provider field, so
a strength routed at another provider cannot be mirrored into it; the agent is printed
with the exemption rather than failed as drift.

This is the same shape of guard as ``check-analyst-mirror.sh``: two files that
must agree, and a script that says so out loud rather than trusting anyone to
remember.

THE PLUGIN'S DEFAULTS, NOT A PROJECT'S ARM. Every resolve here passes
``merge_project=False``: a consuming project's own patch moves an agent
deliberately, for a measured A/B, and the frontmatter is meant to keep saying what
the plugin ships. Run from such a project, the override would otherwise read as
drift between the two readers and fail a config that is exactly as intended.
``check-project-config.sh`` is where the overrides are shown.
"""

from __future__ import annotations

import re
import sys

from .resolve import AGENTS_DIR, ConfigError, agent_frontmatter, load_config, resolve


def activities_of(agent: str, config: dict | None = None) -> list[str]:
    """Every activity this agent performs, in declaration order.

    DERIVED FROM THE ACTIVITY CONFIG, never from the agent's name: the binding lives in
    `activities.<id>.agent` and the relationship between the two names is incidental.
    """
    config = config or load_config(merge_project=False)
    return [aid for aid, spec in (config.get("activities") or {}).items() if spec.get("agent") == agent]


def sync(name: str) -> tuple[str, str] | None:
    """Stamp an agent's `model:`/`effort:` from its tier. Returns (before, after).

    These two fields are DERIVED — fully determined by the agent's `model_tier:`
    and by strengths.yaml — but they are also the fields that EXECUTE on the native path:
    Claude Code reads them when an agent is run as `claude --agent <name>`. (The Agent
    tool path is refused for this plugin's agents by `swarm/guard-agent-tool.sh`, so
    since 0.10.9 the boundary is how nearly every dispatch runs; the mirror guards the
    native path that remains.) Hand-maintaining a derived field that is also an
    operative one is where a costly mistake hides, so it is generated.

    NOT FOR A NON-ANTHROPIC STRENGTH. The frontmatter reader has no provider concept: a
    `qwen/...` id stamped into `model:` would be handed to Anthropic. Such an agent is
    left unstamped, and the check says so rather than reporting drift.

    FROM THE AGENT'S ACTIVITIES, since 0.12.0 — an agent no longer declares a tier. Where an
    agent performs several activities that resolve to DIFFERENT models, there is no single
    value to stamp and the check reports the ambiguity rather than picking one.
    """
    config = load_config(merge_project=False)
    acts = activities_of(name, config)
    if not acts:
        return None
    r = resolve(name, activity=acts[0], config=config)
    if r.provider != "anthropic":
        return None
    path = AGENTS_DIR / f"{name}.md"
    text = path.read_text()
    fm = agent_frontmatter(name)
    before = f"{fm.get('model')}/{fm.get('effort')}"
    if before == f"{r.model}/{r.effort}":
        return None

    # Only ever rewrite inside the frontmatter block. count=1 with a line-anchored
    # pattern keeps an occurrence in the agent's PROSE from being rewritten — the
    # bodies of these files discuss models and effort at length.
    end = text.index("\n---\n", 3)
    head, body = text[:end], text[end:]
    head = re.sub(r"^model: .*$", f"model: {r.model}", head, count=1, flags=re.M)
    head = re.sub(r"^effort: .*$", f"effort: {r.effort}", head, count=1, flags=re.M)
    path.write_text(head + body)
    return before, f"{r.model}/{r.effort}"


def main() -> int:
    write = "--write" in sys.argv[1:]
    try:
        # THE PLUGIN'S SHIPPED DEFAULTS, not the project's patch of them: this compares
        # agent frontmatter with what the plugin declares, and a project's deliberate
        # patch of a strength is not drift — this judges what the plugin ships.
        config = load_config(merge_project=False)
    except ConfigError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    print(f"strengths: {', '.join(sorted(config['strengths']))}   activities: {len(config['activities'])}")

    failures: list[str] = []
    agents = sorted(AGENTS_DIR.glob("*.md"))
    if not agents:
        print(f"FAIL: no agent definitions under {AGENTS_DIR}", file=sys.stderr)
        return 2

    synced: list[str] = []
    for path in agents:
        name = path.stem
        acts = activities_of(name, config)
        if not acts:
            failures.append(
                f"{name}: no activity in strengths.yaml names this agent, so nothing can "
                f"dispatch it on a standard boundary. Declare one under `activities:`."
            )
            continue
        try:
            resolutions = {a: resolve(name, activity=a, config=config) for a in acts}
        except ConfigError as exc:
            failures.append(f"{name}: {exc}")
            continue
        r = resolutions[acts[0]]
        distinct = {(x.model, x.effort) for x in resolutions.values()}

        if write:
            change = sync(name)
            if change:
                synced.append(f"{name}: {change[0]} -> {change[1]}")

        fm = agent_frontmatter(name)
        if "model_tier" in fm:
            failures.append(
                f"{name}: declares `model_tier:`, which is gone (0.12.0). An agent no longer "
                f"picks its own strength — the activity it performs does, in strengths.yaml. "
                f"Remove the key."
            )
        if len(distinct) > 1:
            failures.append(
                f"{name}: performs {', '.join(acts)}, which resolve to different models "
                f"({', '.join(sorted(f'{m} {e}' for m, e in distinct))}). `claude --agent` can "
                f"carry only one, so the frontmatter mirror cannot be stamped unambiguously."
            )
            continue
        if r.provider != "anthropic":
            # The frontmatter reader cannot express a provider; the mirror is exempt and
            # says so — a native `claude --agent` run of this agent would not reach the
            # tier's model, and that is the fact to print, not a drift to fail on.
            print(f"  {name:22s} {'+'.join(acts):34s} {r.strength:7s} {r.provider}/{r.model}  (frontmatter not mirrored: non-Anthropic provider)")
            continue
        for key in ("model", "effort"):
            declared, expected = fm.get(key), getattr(r, key)
            if declared != expected:
                failures.append(
                    f"{name}: frontmatter {key}={declared!r} contradicts strength "
                    f"{r.strength!r} which resolves {key}={expected!r}. `claude --agent` "
                    f"reads frontmatter; the boundary reads the activity — so this "
                    f"agent runs differently depending on how it is dispatched."
                )
        print(f"  {name:22s} {'+'.join(acts):34s} {r.strength:7s} {r.provider}/{r.model} thinking={r.effort}")

    if synced:
        print("\nsynced from strengths.yaml:")
        for line in synced:
            print(f"  {line}")

    if failures:
        print("\nFAIL:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        if not write:
            print(
                "\n  Run `make models-sync` to regenerate model:/effort: from the "
                "activities, or fix the activity itself.",
                file=sys.stderr,
            )
        return 1

    tail = " (nothing to sync)" if write and not synced else ""
    print(
        f"\nOK — {len(agents)} agents, every activity resolves, no frontmatter drift{tail}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
