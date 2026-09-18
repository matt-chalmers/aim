"""Verify the skill layer: every declaration resolves, and nobody is overtaxed.

A preloaded skill is not free reference material — it is a **tax paid on every
dispatch of every agent that declares it**. `campaign-loop` is ~14,000 tokens;
preloading something that size into four lenses would cost 56,000 tokens per task
before any work happened. So this check reports the per-agent preload bill and
fails when one exceeds a budget, rather than letting skills accrete silently.

It also catches the three ways a skill declaration becomes a dead letter:

  * the named skill has no SKILL.md
  * the SKILL.md's `name:` disagrees with its directory (the directory is what the
    frontmatter reference resolves against, so the two disagreeing means one of
    them is lying to a reader)
  * the agent declares `skills:` but lacks the `Skill` tool

The last one is silent by nature — the frontmatter looks right, the agent simply
never receives the doctrine, and behaves like one that was never given it.
"""

from __future__ import annotations

import re
import sys

from .resolve import AGENTS_DIR, _prompts_dir, agent_frontmatter

SKILLS_DIR = _prompts_dir("skills")

#: Per-agent preload ceiling, in characters (~4 chars/token).
#:
#: CALIBRATED TO CATCH THE FAILURE, NOT THE INTENDED CONFIGURATION. `test-doctrine`
#: alone is ~18,400 chars, so any budget under ~23,000 flags every agent that
#: legitimately combines it with anything else — a threshold that fires on the
#: correct setup teaches people to ignore it. 36,000 leaves room for
#: test-doctrine plus two focused skills, while still catching the actual
#: mistake this guards against: preloading something campaign-loop-sized
#: (~56,000 chars) into an agent that runs several times per task.
#:
#: 36,000, from 32,000 (0.10.5). The writers' third skill, `evidence-gathering`, put
#: them at ~35,700 — and it was MEASURED to pay for itself: the same epic, five runs
#: per arm, cost per run $2.40 -> $1.83 (-24%, interquartile ranges apart), output
#: tokens -36%, turns 45 -> 32. A preload is a per-dispatch charge; that one buys
#: fewer, larger tool calls, and the bill went down.
#:
#: Not a hard law — a deliberate exception is fine and should be argued in the
#: commit — but an agent quietly crossing it means every one of its dispatches
#: got dearer and nobody decided that.
BUDGET_CHARS = 36_000

#: A card is injected into every dispatch in its lane, so it is a recurring cost in
#: a way a depth skill is not. Budgeted separately and tightly: without a ceiling a
#: card grows into a preload by stealth, which is the exact tax this layering exists
#: to avoid.
CARD_BUDGET_CHARS = 1_200

#: Technology doctrine is loaded ON DEMAND. A module skill in an agent's `skills:`
#: frontmatter would silently convert a free module into a permanent per-dispatch
#: charge for every project, including those not using that technology.
MODULE_SKILL_PREFIXES = ("stack-", "framework-")


def skill_files() -> dict[str, object]:
    return {p.parent.name: p for p in SKILLS_DIR.glob("*/SKILL.md")}


def main() -> int:
    available = skill_files()
    failures: list[str] = []
    warnings: list[str] = []

    if not available:
        print(f"FAIL: no skills under {SKILLS_DIR}", file=sys.stderr)
        return 2

    # Every skill must declare a name matching its directory.
    print("skills:")
    for name, path in sorted(available.items()):
        text = path.read_text()
        m = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
        if not m:
            failures.append(f"skill {name!r} has no frontmatter")
            continue
        declared = re.search(r"^name:\s*(\S+)", m.group(1), re.M)
        desc = re.search(r"^description:\s*\S", m.group(1), re.M)
        if not declared or declared.group(1) != name:
            failures.append(
                f"skill {name!r} declares name="
                f"{declared.group(1) if declared else '<missing>'!r}; the directory is what a "
                f"frontmatter reference resolves against, so these must agree"
            )
        if not desc:
            failures.append(
                f"skill {name!r} has no description — nothing tells a reader when it applies"
            )
        print(f"  {name:24s} {len(text):>6} chars  ~{len(text) // 4:>5} tokens")

    # Every agent declaration must resolve, and carry the tool that loads it.
    print("\npreload bill per agent:")
    preloads: dict[str, list[str]] = {}
    for path in sorted(AGENTS_DIR.glob("*.md")):
        agent = path.stem
        text = path.read_text()
        declared = re.findall(r"^\s+- (\S+)$", text[: text.index("\n---\n", 3)], re.M)
        if not declared:
            continue
        preloads[agent] = declared

        total = 0
        for s in declared:
            if s not in available:
                failures.append(
                    f"{agent}: declares skill {s!r}, which has no {SKILLS_DIR}/{s}/SKILL.md"
                )
                continue
            total += len(available[s].read_text())

        fm = agent_frontmatter(agent)
        tools = [t.strip() for t in str(fm.get("tools") or "").split(",")]
        if "Skill" not in tools:
            failures.append(
                f"{agent}: declares skills {declared} but has no `Skill` tool — the "
                f"declaration is a dead letter and the agent silently runs without the doctrine"
            )

        flag = ""
        if total > BUDGET_CHARS:
            flag = f"  !! over budget ({BUDGET_CHARS:,} chars)"
            warnings.append(
                f"{agent}: {total:,} chars preloaded, budget {BUDGET_CHARS:,}"
            )
        print(
            f"  {agent:24s} {total:>6} chars  ~{total // 4:>5} tokens  {','.join(declared)}{flag}"
        )

    # --- the guarantee that lets modules accumulate without bound ---------------
    from .project import load as _load

    try:
        proj = _load()
    except Exception:  # a project may not be configured; the other checks report it
        proj = None

    if proj is not None:
        for agent, declared in preloads.items():
            offenders = [d for d in declared if d.startswith(MODULE_SKILL_PREFIXES)]
            if offenders:
                failures.append(
                    f"{agent} preloads technology skill(s) {offenders}. Module doctrine is "
                    f"loaded on demand — preloading it charges EVERY dispatch, including "
                    f"projects that do not use that technology. Remove it from `skills:`; "
                    f"the card already carries what a worker needs constantly."
                )

        cards: list[tuple[str, str]] = [
            (st.name, st.card_raw) for st in proj.stacks if st.card_raw
        ]
        cards += [
            (f.get("name", "?"), (f.get("card") or "").strip())
            for f in proj.framework_configs()
            if (f.get("card") or "").strip()
        ]
        for name, card in cards:
            if len(card) > CARD_BUDGET_CHARS:
                failures.append(
                    f"card for {name!r} is {len(card):,} chars, over the "
                    f"{CARD_BUDGET_CHARS:,} budget. A card is paid on every dispatch in "
                    f"its lane; move the depth into its doctrine skill."
                )
        if cards:
            print(f"\ncards: {', '.join(f'{n} ({len(c):,}c)' for n, c in cards)}")

        for mod in list(proj.stacks):
            skill = mod.raw.get("doctrine_skill")
            if skill and skill not in available:
                failures.append(f"stack {mod.name!r} points at missing skill {skill!r}")
        for f in proj.framework_configs():
            skill = f.get("doctrine_skill")
            if skill and skill not in available:
                failures.append(
                    f"framework {f.get('name')!r} points at missing skill {skill!r}"
                )

    if warnings:
        print("\nOVER BUDGET:", file=sys.stderr)
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)
        print(
            "  A preloaded skill is paid on every dispatch. Trim it, or argue the "
            "exception in the commit message.",
            file=sys.stderr,
        )
    if failures:
        print("\nFAIL:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    if warnings:
        print(
            f"\nWARN — {len(available)} skills, every declaration resolves, but "
            f"{len(warnings)} agent(s) are over the preload budget."
        )
        return 1
    print(
        f"\nOK — {len(available)} skills, every declaration resolves, all agents within budget."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
