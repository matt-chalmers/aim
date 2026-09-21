"""Fail loudly when the model routing config and the agent definitions disagree.

WHY THIS EXISTS. The tier lives in ``tiers.yaml``; each agent declares which tier
it wants; and each agent ALSO carries ``model:``/``effort:``, which is what Claude
Code reads when the agent is dispatched natively through the Agent tool rather
than through the boundary. Two readers, one intent. Let them drift and the same
agent runs a different model depending on how it was called — the worst kind of
bug, because both paths work and only the bill and the quality differ.

This is the same shape of guard as ``check-analyst-mirror.sh``: two files that
must agree, and a script that says so out loud rather than trusting anyone to
remember.

THE PLUGIN'S DEFAULTS, NOT A PROJECT'S ARM. Every resolve here passes
``project_tiers={}``: a consuming project's ``agent_tiers:`` block moves an agent
deliberately, for a measured A/B, and the frontmatter is meant to keep saying what
the plugin ships. Run from such a project, the override would otherwise read as
drift between the two readers and fail a config that is exactly as intended.
``check-project-config.sh`` is where the overrides are shown.
"""

from __future__ import annotations

import re
import sys

from .resolve import AGENTS_DIR, ConfigError, agent_frontmatter, load_config, resolve


def sync(name: str) -> tuple[str, str] | None:
    """Stamp an agent's `model:`/`effort:` from its tier. Returns (before, after).

    These two fields are DERIVED — fully determined by the agent's `model_tier:`
    and by tiers.yaml — but they are also the fields that actually EXECUTE: Claude
    Code reads them whenever an agent is dispatched through the Agent tool or
    `claude --agent`, which is most dispatches. Hand-maintaining a derived field
    that is also the operative one is where a costly mistake hides, so it is
    generated, in the same spirit as any generated-artifact refresh.
    """
    r = resolve(name, project_tiers={}, config=load_config(merge_project=False))
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
        # redefinition of a tier is not drift — the same reason `project_tiers={}` below.
        config = load_config(merge_project=False)
    except ConfigError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    tiers = config["tiers"]
    print(f"tiers: {', '.join(sorted(tiers))}   default: {config['default_tier']}")

    failures: list[str] = []
    agents = sorted(AGENTS_DIR.glob("*.md"))
    if not agents:
        print(f"FAIL: no agent definitions under {AGENTS_DIR}", file=sys.stderr)
        return 2

    synced: list[str] = []
    for path in agents:
        name = path.stem
        try:
            r = resolve(name, config=config, project_tiers={})
        except ConfigError as exc:
            failures.append(f"{name}: {exc}")
            continue

        if write:
            change = sync(name)
            if change:
                synced.append(f"{name}: {change[0]} -> {change[1]}")

        fm = agent_frontmatter(name)
        if fm.get("model_tier") is None:
            failures.append(
                f"{name}: no model_tier declared, so it silently takes the global "
                f"default ({config['default_tier']}). Declare it."
            )
        for key in ("model", "effort"):
            declared, expected = fm.get(key), getattr(r, key)
            if declared != expected:
                failures.append(
                    f"{name}: frontmatter {key}={declared!r} contradicts tier "
                    f"{r.tier!r} which resolves {key}={expected!r}. The Agent tool "
                    f"reads frontmatter; the boundary reads the tier — so this "
                    f"agent runs differently depending on how it is dispatched."
                )
        print(f"  {name:22s} {r.tier:10s} {r.provider}/{r.model} effort={r.effort}")

    if synced:
        print("\nsynced from tiers.yaml:")
        for line in synced:
            print(f"  {line}")

    if failures:
        print("\nFAIL:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        if not write:
            print(
                "\n  Run `make models-sync` to regenerate model:/effort: from the "
                "tiers, or fix the tier declaration itself.",
                file=sys.stderr,
            )
        return 1

    tail = " (nothing to sync)" if write and not synced else ""
    print(
        f"\nOK — {len(agents)} agents, every tier resolves, no frontmatter drift{tail}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
