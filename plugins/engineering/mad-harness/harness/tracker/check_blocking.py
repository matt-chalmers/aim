"""Find records whose TEXT claims a blocking relationship that has no dependency EDGE.

A note saying "GATED ON X" gates nothing. The scheduler reads edges, not prose, so a task
blocked only in prose is offered to a worker who then hits the unanswerable question the
note warned about. Three instances were found in a single session, each stating its
blocker plainly while the ready queue still offered it.

DELIBERATELY NARROW. A prose claim only counts as broken if the task it says is blocked is
actually dispatchable RIGHT NOW. Blocking via a gate is honoured by the queue and is not a
defect, so gate-blocked tasks are silently correct and never reported. That is what makes
this worth running: everything it prints contradicts its own text today.

THE ID PREFIX IS DERIVED FROM THE DATA, NEVER WRITTEN AS A LITERAL. A check that hardcodes
one matches nothing in a repository using a different prefix and reports CLEAN — which is
indistinguishable from having examined everything and found no problem. It does not error,
so nobody looks.
"""

from __future__ import annotations

import re
import sys

import tracker
from tracker.port import OPEN_STATUSES, Task

#: "this record blocks the named one" versus "this record is blocked by the named one".
#: The direction decides which way the missing edge should point.
BLOCKS = (r"\bBlocks?\s+({id})", r"BEFORE\s+({id})\s+IS\s+DISPATCHED", r"\bgating\s+({id})")
BLOCKED_BY = (
    r"GATED ON\s+({id})",
    r"\b[Bb]locked (?:on|behind)\s+({id})",
    r"\bdepends on\s+({id})",
)
#: A sentence may name its OWN subject — "<id> is blocked on <other>" inside a note on some
#: THIRD record. Crediting the containing record there is a false positive, and a lint that
#: cries wolf gets ignored. Matched first, then masked out before the generic pass.
SUBJECTED = (r"({id})\s*(?:\([^)]*\)\s*)?is\s+blocked\s+(?:on|behind)\s+({id})",)


def _id_pattern(tasks: list[Task]) -> str | None:
    prefixes = {t.id.rsplit("-", 1)[0] for t in tasks if "-" in t.id}
    if not prefixes:
        return None
    return "(?:" + "|".join(re.escape(p) for p in sorted(prefixes)) + r")-[\w.]+"


def findings(store=None) -> list[tuple[str, str, str, str, str, str]]:
    store = store or tracker.task_store()
    tasks = store.list()
    if not tasks:
        raise tracker.TrackerError(
            "the tracker returned no records — nothing was examined, so this is not a "
            "clean result"
        )
    idpat = _id_pattern(tasks)
    if idpat is None:
        raise tracker.TrackerError(
            "no record ids found — cannot derive the id prefix, so refusing to report clean"
        )

    by = {t.id: t for t in tasks}
    ready = {t.id for t in store.ready(limit=500)}
    out: list[tuple[str, str, str, str, str, str]] = []

    def real(dependent: str, blocker: str) -> bool:
        """A claim is broken only if both ends are open, the edge is missing, and the
        dependent is dispatchable right now."""
        return (
            dependent in by
            and blocker in by
            and by[dependent].status in OPEN_STATUSES
            and by[blocker].status in OPEN_STATUSES
            and blocker not in by[dependent].depends_on
            and dependent in ready
        )

    for t in tasks:
        if t.status not in OPEN_STATUSES:
            continue
        text = f"{t.description}\n{t.notes}"
        for pat in SUBJECTED:
            rx = pat.format(id=idpat)
            for m in re.finditer(rx, text):
                dependent, blocker = m.group(1), m.group(2)
                if real(dependent, blocker):
                    ctx = re.sub(r"\s+", " ", text[max(0, m.start() - 90) : m.end() + 70]).strip()
                    out.append((t.id, "blocked_by", blocker, dependent, blocker, ctx))
            text = re.sub(rx, " ", text)

        for pats, direction in ((BLOCKS, "blocks"), (BLOCKED_BY, "blocked_by")):
            for pat in pats:
                for m in re.finditer(pat.format(id=idpat), text):
                    other = m.group(1)
                    if other == t.id or other not in by:
                        continue
                    dependent, blocker = (
                        (other, t.id) if direction == "blocks" else (t.id, other)
                    )
                    if real(dependent, blocker):
                        ctx = re.sub(
                            r"\s+", " ", text[max(0, m.start() - 90) : m.end() + 70]
                        ).strip()
                        out.append((t.id, direction, other, dependent, blocker, ctx))

    seen, uniq = set(), []
    for f in out:
        key = (f[3], f[4])
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    return uniq


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    strict = "--strict" in args
    try:
        uniq = findings()
    except tracker.TrackerError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if not uniq:
        print("No blocking claim in prose lacks a matching edge.")
        return 0

    print(f"{len(uniq)} blocking claim(s) stated in prose with NO dependency edge:\n")
    for tid, direction, other, dependent, blocker, ctx in uniq:
        says = "blocks" if direction == "blocks" else "is blocked by"
        print(f"  {tid}  says it {says} {other}")
        print(f"    ...{ctx}...")
        print(f"    FIX (if real):  tk.sh dep {dependent} {blocker}")
        print()
    print("Every record above is dispatchable NOW, despite its own text saying it is blocked.")
    print("Judge each: add the edge, or reword the note so it does not read as a gate.")
    return 1 if strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
