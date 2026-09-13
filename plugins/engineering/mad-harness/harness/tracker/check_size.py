"""Warn before a record hits the ceiling that write-locks it.

WHY THIS IS A CAPABILITY QUESTION AND NOT A CONSTANT. beads keeps description and notes
in one record and refuses past roughly 64KB, and it fails CLOSED with no warning — a
38-character append fails exactly as a 3KB one does. One epic task reached 64,244
characters and now rejects EVERY update. The campaign loop writes to epic notes on every
run, so a write-locked epic silently loses state the next run reads as current.

That ceiling is a fact about tasks, not about tracking. A backend storing one file per
task has no such limit, and a check hardcoding 64,000 would warn about nothing for the
rest of time — the shape of guard this repo already refuses elsewhere. So the number
comes from `capabilities().record_bytes`, and a backend declaring None disables the check
with a line saying why rather than silently passing.
"""

from __future__ import annotations

import sys

import tracker
from tracker.port import CLOSED

#: How close to the ceiling is worth saying out loud. Roughly two-thirds: far enough out
#: that a task can still be split before the wall, near enough that it is not noise.
WARN_FRACTION = 0.625


def rows(store=None) -> tuple[list[tuple[int, str, str, str]], int]:
    store = store or tracker.task_store()
    ceiling = store.capabilities().record_bytes
    if not ceiling:
        return [], 0
    warn = int(ceiling * WARN_FRACTION)
    out = []
    for t in store.list():
        if t.status == CLOSED:
            continue  # a closed record is never appended to again
        n = len(t.description) + len(t.notes)
        if n >= warn:
            out.append((n, t.id, t.type, t.title[:52]))
    return sorted(out, reverse=True), ceiling


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    strict = "--strict" in args

    store = tracker.task_store()
    ceiling = store.capabilities().record_bytes
    if not ceiling:
        print(
            f"backend {store.capabilities().name!r} declares no record ceiling — "
            f"nothing to warn about. (This check exists for backends that fail closed "
            f"past a size limit.)"
        )
        return 0

    # AN EMPTY TRACKER IS NOT A CLEAN ONE. A check that examined nothing and printed
    # "nothing to report" is indistinguishable from one that examined everything and
    # found no problem — the failure this repo guards against in every other sweep.
    everything = store.list()
    if not everything:
        print(
            "no records returned by the tracker — nothing was examined, so this is NOT "
            "a clean result. Is a tracker configured for this repository?",
            file=sys.stderr,
        )
        return 2

    found, _ = rows(store)
    if not found:
        print(f"No open record is within {ceiling - int(ceiling * WARN_FRACTION):,} characters of the ceiling.")
        return 0

    locked = [r for r in found if r[0] >= ceiling]
    print(f"{len(found)} open record(s) at or approaching the ~{ceiling:,}-char ceiling:\n")
    for n, tid, ttype, title in found:
        state = "WRITE-LOCKED" if n >= ceiling else f"{ceiling - n:,} left"
        print(f"  {n:>7}  {state:<14} {tid:<18} {ttype:<8} {title}")
    print()
    print("Move accumulating run content to the epic's staged run-log, which is deleted at")
    print("epic close. Durable content (owner decisions, findings) folds into the corpus or")
    print("becomes its own record — it must NOT go to the staging folder, which does not")
    print("survive close.")
    if locked:
        print(f"\n{len(locked)} record(s) already reject every update.")
    return 1 if (strict and found) else 0


if __name__ == "__main__":
    raise SystemExit(main())
