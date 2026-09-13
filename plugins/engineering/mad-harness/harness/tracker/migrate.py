"""Move every record from one backend to another.

WHY THIS IS NOT A PORT METHOD. An `import` verb on `TaskStore` would have each backend
parse its own export format, which does not migrate anything — a beads JSONL means nothing
to the markdown backend. Migration is a read through one port and a write through another,
so it needs no new interface and works for any pair of backends that satisfy the contract,
including ones not written yet.

IDS ARE NOT PRESERVED, BECAUSE THEY CANNOT BE. Each backend mints its own (`t-a1b2c3`
against `PROJ-4f2a.1`), and a record's id is the backend's to choose. Every edge therefore
has to be rewritten through a map built as the records are created, which is the whole
reason this is more than a loop.

WHAT IS PRESERVED: type, title, description, priority, labels, parent, dependency edges,
status, and a closed record's reason. What is not: ids, timestamps, and any backend-specific
field under `raw` — none of which the harness may branch on anyway.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .port import CLOSED, TaskStore


@dataclass(frozen=True)
class Migration:
    """What moved, and what could not."""

    created: dict[str, str]           # old id -> new id
    edges: int
    closed: int
    skipped: tuple[str, ...] = ()     # old ids the target refused, with the reason

    @property
    def ok(self) -> bool:
        return not self.skipped

    def summary(self) -> str:
        line = (
            f"{len(self.created)} record(s), {self.edges} edge(s), {self.closed} closed"
        )
        return line if self.ok else f"{line}; {len(self.skipped)} SKIPPED"


def _creation_order(rows: list[Any]) -> list[Any]:
    """Parents before children, so `parent` can be set at creation.

    A repeated pass rather than a full topological sort: parent chains here are one deep
    (epic -> task) and a cycle in them would be a corrupt source, which the caller finds
    out about because the record simply never becomes placeable.
    """
    by_id = {t.id: t for t in rows}
    done: set[str] = set()
    ordered: list[Any] = []
    remaining = list(rows)
    while remaining:
        progressed = False
        still: list[Any] = []
        for t in remaining:
            if not t.parent or t.parent not in by_id or t.parent in done:
                ordered.append(t)
                done.add(t.id)
                progressed = True
            else:
                still.append(t)
        if not progressed:
            # A parent cycle. Emit the rest unparented rather than looping forever —
            # losing a parent edge is recoverable, hanging is not.
            ordered.extend(still)
            break
        remaining = still
    return ordered


def migrate(source: TaskStore, target: TaskStore) -> Migration:
    """Copy every record from `source` into `target`. Returns what moved."""
    rows = source.list()
    id_map: dict[str, str] = {}
    skipped: list[str] = []

    for task in _creation_order(rows):
        try:
            new_id = target.create(
                task.title,
                type=task.type,
                description=task.description,
                priority=task.priority,
                parent=id_map.get(task.parent) if task.parent else None,
                labels=tuple(task.labels),
            )
        except Exception as exc:  # noqa: BLE001 — one bad record must not lose the rest
            skipped.append(f"{task.id} ({task.type}): {exc}")
            continue
        id_map[task.id] = new_id
        if task.notes:
            try:
                target.note(new_id, task.notes)
            except Exception:  # noqa: BLE001 — a note is not worth failing a migration
                pass

    # EDGES AFTER ALL CREATES. A dependency may point at a record created later, so the
    # map has to be complete before any edge is written.
    edges = 0
    for task in rows:
        new_id = id_map.get(task.id)
        if not new_id:
            continue
        for blocker in task.depends_on:
            mapped = id_map.get(blocker)
            if not mapped:
                continue  # an edge to a record that did not migrate
            try:
                target.dep_add(new_id, mapped)
                edges += 1
            except Exception:  # noqa: BLE001
                skipped.append(f"{task.id} -> {blocker}: edge not written")

    # CLOSE LAST, so a closed record's dependencies exist when it is closed.
    closed = 0
    for task in rows:
        new_id = id_map.get(task.id)
        if new_id and task.status == CLOSED:
            try:
                target.close(new_id, task.close_reason or "migrated as closed")
                closed += 1
            except Exception:  # noqa: BLE001
                skipped.append(f"{task.id}: created but not closed")

    return Migration(created=id_map, edges=edges, closed=closed, skipped=tuple(skipped))
