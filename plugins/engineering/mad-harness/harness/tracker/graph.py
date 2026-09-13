"""Dependency-graph answers, computed from records rather than asked of a backend.

WHY SHARED RATHER THAN PER-BACKEND. `ready` and `validate` are pure functions of the task
set: which blockers are closed, which levels the DAG falls into, where the cycles are. A
backend that answers them natively is a convenience, not a requirement, and computing them
here means a new backend inherits both for free and cannot disagree with its siblings
about what "ready" means.

WAVES ARE DEPENDENCY PARALLELISM AND NOTHING ELSE. `max_parallelism` says how wide the
graph *allows*, not how wide a wave should be. One recorded epic validated 11-wide while
five of its six wave-1 tasks touched the same shared module. Every caller reporting the
number must say so.
"""

from __future__ import annotations

from .port import CLOSED, OPEN_STATUSES, Task, Validation, Wave


def _by_id(tasks: list[Task]) -> dict[str, Task]:
    return {t.id: t for t in tasks}


def ready(tasks: list[Task], *, gated: set[str] | None = None) -> list[Task]:
    """Open tasks whose every blocker is closed and which no gate holds.

    A blocker that is ABSENT from the set is treated as satisfied, deliberately: a task
    may depend on one closed long ago and since archived, and refusing to schedule it
    forever would be worse than the alternative. A blocker that is PRESENT and open
    blocks. The two cases are distinguishable only because `show` still resolves closed
    ids — which is why that is a rule of the port rather than an implementation detail.
    """
    gated = gated or set()
    known = _by_id(tasks)
    out = []
    for t in tasks:
        if t.status not in OPEN_STATUSES or t.id in gated:
            continue
        blocked = any(
            (b in known and known[b].status != CLOSED) for b in t.depends_on
        )
        if not blocked:
            out.append(t)
    return out


def validate(tasks: list[Task]) -> Validation:
    """Level the DAG into waves, and name what is wrong with it.

    Kahn's algorithm over open tasks. Anything still unplaced when no node has a
    satisfied in-edge is part of a cycle — reported rather than raised, because a caller
    wants the whole picture, not the first problem.
    """
    known = _by_id(tasks)
    pending = {t.id: set(b for b in t.depends_on if b in known) for t in tasks}
    # A closed blocker is satisfied already and never holds a wave back.
    for tid, deps in pending.items():
        pending[tid] = {d for d in deps if known[d].status != CLOSED}

    open_ids = {t.id for t in tasks if t.status in OPEN_STATUSES}
    pending = {k: v for k, v in pending.items() if k in open_ids}

    orphans = tuple(
        sorted(
            b
            for t in tasks
            if t.status in OPEN_STATUSES
            for b in t.depends_on
            if b not in known
        )
    )

    waves: list[Wave] = []
    placed: set[str] = set()
    index = 0
    while pending:
        layer = sorted(k for k, deps in pending.items() if not (deps - placed))
        if not layer:
            break  # everything left is in a cycle
        waves.append(Wave(index=index, task_ids=tuple(layer)))
        placed.update(layer)
        for k in layer:
            pending.pop(k)
        index += 1

    cycles: tuple[tuple[str, ...], ...] = ()
    if pending:
        cycles = (tuple(sorted(pending)),)

    return Validation(waves=tuple(waves), cycles=cycles, orphans=orphans)
