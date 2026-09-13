"""Resolve which technology modules are active, and render their cards.

THE CONTEXT-BUDGET PROBLEM THIS SOLVES. A preloaded skill is a tax paid on every
dispatch whether or not it is read. `fullstack-engineer` already preloads ~6,300
tokens against a 32,000-char budget. If each supported technology added ~1,500
preloaded tokens, six of them would add ~9,000 tokens to EVERY dispatch — and a
Python task would pay to carry Go, Rust and Ruby knowledge it will never open.

So technology knowledge is layered by what it costs:

    L0  values      stacks/*.yaml, frameworks/*.yaml   0, machine-read
    L1  card        rendered here, ACTIVE modules only  ~250 tok, no tool call
    L2  doctrine    skills/{stack,framework}-*          0 until invoked
    L3  generic     the preloaded skills                unchanged

The card is the move that makes this cheap. Measured cost model:
`tokens ~= 18,700 + 2,600 x tool_calls`. A card costs ~250 tokens and NO tool call;
loading a skill on demand costs a whole call; preloading costs forever. So the few
facts a worker needs constantly go in the card, and everything else waits in a
skill that costs nothing until someone asks.

The invariant that lets this grow without bound, enforced by test:

    Adding a module must not change ANY agent's preload bill.
"""

from __future__ import annotations

from dataclasses import dataclass

from .project import Project, load


@dataclass(frozen=True)
class Module:
    """A technology module — a toolchain, a framework, or the fidelity axis."""

    name: str
    kind: str  # "stack" | "framework" | "fidelity"
    card: str
    doctrine_skill: str | None


def _module(raw: dict, kind: str) -> Module:
    return Module(
        name=raw.get("name", "?"),
        kind=kind,
        card=(raw.get("card") or "").strip(),
        doctrine_skill=raw.get("doctrine_skill"),
    )


def active_modules(lane: str | None, project: Project | None = None) -> list[Module]:
    """The modules whose rules belong in this dispatch's prompt.

    EVERY DECLARED MODULE, unless a lane explicitly narrows it. A stack is what the
    repository is built with; a lane is what kind of work this is. They are orthogonal,
    and for execution there is no selection at all — `worker.bootstrap` and
    `Project.worker_env` both iterate every declared stack, because a worker may touch
    anything.

    Selection exists only here, and it is a context trim rather than routing. Measured
    on a two-stack, two-framework repository the full card is ~500 tokens against a
    dispatch of 20,000+: filtering saves about 1% while risking the withholding of the
    one rule that would have prevented a mistake. So narrowing is opt-in, for a
    repository with enough modules to care, and the default errs toward telling the
    agent too much.
    """
    p = project or load()
    lanes = p.raw.get("lanes") or {}
    wanted = lanes.get(lane) if lane else None
    if wanted and not (wanted.get("stacks") or wanted.get("frameworks")):
        wanted = None  # a lane declaring only agent/cap narrows nothing

    stacks = [_module(s.raw, "stack") for s in p.stacks if s.card_raw]
    frameworks = [_module(f, "framework") for f in p.framework_configs()]
    everything = stacks + frameworks

    if wanted:
        names = set(wanted.get("stacks") or []) | set(wanted.get("frameworks") or [])
        everything = [m for m in everything if m.name in names] or everything

    # Design fidelity is a third axis, and it behaves differently on purpose. A stack or
    # framework defaults to active because a worker may touch anything; fidelity is a
    # distinct ACTIVITY that a lane either does or does not do. So it is opt-in, and a
    # project with no handover never sees it at all.
    #
    # Appended AFTER the narrowing rather than inside it: a fidelity lane that also
    # declares stacks would otherwise have the fidelity card filtered straight back out,
    # because the narrowing matches stack and framework names only.
    fid = p.raw.get("fidelity") or {}
    if fid.get("card") and (lanes.get(lane) or {}).get("fidelity"):
        everything = everything + [_module({**fid, "name": "fidelity"}, "fidelity")]

    return everything


def render_card(lane: str | None, project: Project | None = None) -> str:
    """The prompt fragment for `lane`, or "" when nothing is declared.

    Empty is a legitimate answer — a project with no cards should have nothing
    appended to its prompts, not a header explaining that there is nothing.
    """
    mods = [m for m in active_modules(lane, project) if m.card]
    if not mods:
        return ""
    out = [
        "## Technology context",
        "",
        f"The stack and framework rules for this lane ({lane or 'all'}). These are the",
        "few that are wrong often enough to be worth stating up front; for depth, load",
        "the skill named beside each.",
        "",
    ]
    for m in mods:
        out.append(m.card)
        if m.doctrine_skill:
            out.append(f"  (depth: skill `{m.doctrine_skill}`)")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    import sys

    args = argv if argv is not None else sys.argv[1:]
    lane = args[0] if args and args[0] else None
    card = render_card(lane)
    if not card:
        print(f"no technology modules declare a card for lane {lane or '(all)'}.")
        return 0
    print(card, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
