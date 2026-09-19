"""What the harness needs from a task tracker, stated once so a backend can be swapped.

WHY FOUR PORTS AND NOT ONE. The obvious factoring — one `TaskStore` covering everything
`bd` does — bakes in an accident of the current backend. Two of its responsibilities are
not storage at all:

    claim / merge-slot   coordinate THIS MACHINE's worker processes. Every wave runs
                         eight worktrees on one filesystem, whatever tracker holds the
                         records, so the mutex has nothing to do with the backend.
    telemetry            high volume, low value per record. It lives in tasks today only
                         because beads happens to store events AS tasks.

Pulling both out means one implementation each instead of one per backend, and it is the
split that becomes expensive to make once two adapters have each grown their own claim
logic.

PORT PURITY, AND WHY IT IS A RULE RATHER THAN A HABIT. No `TaskStore` method takes or
returns a filesystem path. Both shipped backends happen to sit on a local POSIX
filesystem, so nothing would *fail* if a path leaked into a signature — it would simply
become load-bearing, unnoticed, until the next adapter had to reproduce it. Pinned by
test_port_exposes_no_filesystem_paths.

WHAT A BACKEND MAY LEGITIMATELY NOT HAVE. Capabilities are declared, never guessed. A
caller asks `caps.record_bytes` rather than assuming the ~64KB ceiling that is a tasks
constraint, and a check that reads it from config keeps working when the backend changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

#: Issue types the harness itself reasons about. A backend may know others; these are the
#: ones prompts and checks name, so they are the ones that must round-trip.
EPIC = "epic"
TASK = "task"
DECISION = "decision"
#: The title prefix that marks a `decision` as a PERMISSION REQUEST.
#:
#: Not a record type of its own: `bd` validates its type vocabulary and rejects unknown
#: ones — "invalid issue type: permission" — so a new type would work on one backend and
#: fail on the other. A permission request is structurally a decision anyway: a question a
#: swarm cannot answer for itself and an operator must. The prefix keeps the queue's
#: composition countable, which is the signal that matters — a backlog filling with
#: permission requests rather than product questions means the permission model is wrong.
PERMISSION_PREFIX = "Permission:"
GATE = "gate"

#: Statuses the scheduler branches on. `ready` is derived, never stored.
OPEN = "open"
IN_PROGRESS = "in_progress"
BLOCKED = "blocked"
CLOSED = "closed"

OPEN_STATUSES = (OPEN, IN_PROGRESS, BLOCKED)


class TrackerError(RuntimeError):
    """The tracker could not answer, and the caller must not proceed as if it had."""


class NotSupported(TrackerError):
    """This backend does not implement a capability the caller asked for.

    Raised rather than returned empty. An unsupported operation that answers `[]` is
    indistinguishable from one that genuinely found nothing — the empty-reads-as-clean
    failure the harness guards against everywhere else.
    """


@dataclass(frozen=True)
class Capabilities:
    """What this backend can do, declared rather than inferred."""

    name: str
    #: Bytes a single record can hold before writes fail, or None for no ceiling.
    #: beads refuses past roughly 64KB and does so SILENTLY-CLOSED; markdown has no limit.
    record_bytes: int | None = None
    #: `swarm create` registers a molecule for `ready --mol`. tasks-only.
    molecules: bool = False
    #: A curated workflow-context dump (`bd prime`). tasks-only.
    prime: bool = False
    #: Whether the backend keeps its own git-tracked artefact that one actor syncs.
    tracked_export: bool = True
    #: Repo-relative prefixes this backend writes and OWNS. Declared rather than
    #: guessed, because every consumer that must skip them — a doc-drift sweep over
    #: `docs/**`, a differential comparing two backends' output, a `.gitignore`
    #: generator — otherwise hardcodes `.beads/` and silently breaks on the next
    #: backend. Trailing slash marks a directory.
    owned_paths: tuple[str, ...] = ()
    #: The repo-relative path `export()` writes with no argument — the ONE artefact the
    #: close-out commits — or None when the backend keeps no tracked export. Narrower than
    #: `owned_paths` on purpose: for beads that prefix also holds `config.yaml`, which the
    #: autosync toggle rewrites, so `git add .beads/` would commit the wave's
    #: `export.auto: false` and leave the tree dirty again the moment §5 restores it.
    export_path: str | None = None


@dataclass(frozen=True)
class Task:
    """One tracker record, in the shape the harness reasons about.

    `raw` carries the backend's own payload for anything not modelled here. Callers may
    read it for diagnostics; nothing in the harness may branch on it, or the abstraction
    is decorative.
    """

    id: str
    type: str
    status: str
    title: str = ""
    description: str = ""
    #: What the verifier checks against. A FIRST-CLASS FIELD for the same reason as
    #: `close_reason`: campaign-loop §3c tells the planner to "write the missing criteria"
    #: and §3d judges the plan by them, and the port offered no way to write them — the
    #: documented workflow had to reach around to `bd update --acceptance`, which
    #: defeats --readonly and does not exist under mdfiles at all.
    acceptance: str = ""
    notes: str = ""
    priority: int | None = None
    parent: str | None = None
    depends_on: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    assignee: str | None = None
    created_at: str = ""
    updated_at: str = ""
    #: Why this record was closed, as the closer gave it.
    #:
    #: A FIRST-CLASS FIELD because callers need it and `raw` is explicitly off limits —
    #: "nothing in the harness may branch on it, or the abstraction is decorative". Every
    #: backend already stored the reason somewhere; the conformance contract searched
    #: description, notes, title AND raw for it, which was the contract admitting the port
    #: had no answer. A worker resuming after an operator refused its request needs to be
    #: told WHY, and that is a port question, not a diagnostic one.
    close_reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    def gloss(self) -> str:
        """`<id> (<short gloss>)` — the form the harness conventions require in prose."""
        short = " ".join(self.title.split()[:6])
        return f"{self.id} ({short})" if short else self.id


@dataclass(frozen=True)
class Wave:
    """One level of the dependency DAG: tasks with no unsatisfied blockers among peers."""

    index: int
    task_ids: tuple[str, ...]


@dataclass(frozen=True)
class Validation:
    """The result of checking an epic's DAG. `waves` is dependency-only parallelism.

    IT IGNORES FILE CONTENTION, and every caller must say so when reporting the number.
    An epic has been rated 11-wide whose file graph supported about two.
    """

    waves: tuple[Wave, ...]
    cycles: tuple[tuple[str, ...], ...] = ()
    orphans: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.cycles and not self.orphans

    @property
    def max_parallelism(self) -> int:
        return max((len(w.task_ids) for w in self.waves), default=0)


@runtime_checkable
class TaskStore(Protocol):
    """The records. The only port with a per-backend implementation of substance."""

    def capabilities(self) -> Capabilities: ...

    # --- reads ---
    def show(self, task_id: str) -> Task | None:
        """One record, or None. MUST still resolve a closed id.

        A reference to a closed task resolving as None makes "dependency satisfied"
        indistinguishable from "dependency missing".
        """
        ...

    def list(
        self,
        *,
        type: str | None = None,
        status: str | None = None,
        parent: str | None = None,
        limit: int | None = None,
    ) -> list[Task]: ...

    def ready(self, *, parent: str | None = None, limit: int | None = None) -> list[Task]:
        """Dispatchable now: open, every blocker closed, no gate holding it."""
        ...

    def validate(self, epic_id: str) -> Validation: ...

    # --- writes ---
    def create(
        self,
        title: str,
        *,
        type: str = TASK,
        description: str = "",
        priority: int | None = None,
        parent: str | None = None,
        labels: tuple[str, ...] = (),
    ) -> str: ...

    def update(self, task_id: str, **fields: Any) -> None: ...

    def close(self, task_id: str, reason: str) -> None:
        """Close with a reason. A reason is REQUIRED — a bare close records nothing."""
        ...

    def note(self, task_id: str, text: str) -> None:
        """Append to the record's audit trail. Never replaces existing notes."""
        ...

    def dep_add(self, dependent: str, blocker: str) -> None: ...

    def supersede(self, old_id: str, new_id: str) -> None: ...

    def delete(self, task_id: str) -> None: ...

    # --- gates ---
    def gate_create(self, blocks: str, reason: str) -> str: ...

    def gate_list(self) -> list[Task]: ...

    def gate_resolve(self, gate_id: str) -> None: ...

    # --- the tracked artefact ---
    def export(self) -> None:
        """Regenerate the git-tracked representation. ONE actor, once per wave."""
        ...

    def autosync(self, enabled: bool) -> None:
        """Whether the backend may write its tracked artefact on its own.

        A wave turns this OFF at pre-flight and back on at close. tasks stages
        `issues.jsonl` into whatever commit happens next unless told not to, which lands
        one worker's tracker state in a sibling's commit. A backend that only ever exports
        when asked has nothing to do here and says so by succeeding.
        """
        ...

    def prime(self) -> str:
        """A curated workflow-context dump, if the backend has one.

        Raises `NotSupported` otherwise — never an empty string, which would read as
        "primed, and there was nothing to say".
        """
        ...

    def label(self, task_id: str, name: str, *, remove: bool = False) -> None:
        """Add or remove one label."""
        ...


#: What `park` records on the epic, so `unpark` can find the gate it made without
#: asking the backend — which, on beads, cannot say. Same pattern as `resume.VERIFIED`.
PARKED = re.compile(r"\bPARKED\s+(?P<gate>\S+):")


def park(store: TaskStore, epic_id: str, reason: str) -> str:
    """Park an epic: gate it AND take it out of the open queue, as one operation.

    THE TWO-STEP INVARIANT THIS CLOSES. `gate create <epic>` alone does NOT park an epic on
    beads: it refuses the blocking edge ("epics can only block other epics, not tasks")
    while still writing the gate issue, so the epic stays `open` and the loop's queue —
    `list --type epic --status open` — offers it again. The loop's text therefore said
    "gate create AND update --status blocked — both steps, always" in four places, the
    pinned block repeated it after every compaction, and `commands/campaign-auto.md` still
    shipped with only the first step. A rule stated four times and drifted once is a verb
    the tracker should own; this is that verb.

    Order: the gate first (it carries the reason and the audit trail), then a `PARKED
    <gate>: <reason>` note on the epic so `unpark` can find the gate — beads cannot
    record which epic a gate blocks, and `gate_list` is every open gate in the store — and
    the status write last, since it is the step that changes what the queue offers.
    Refuses an epic that is already `blocked`: a second park would stack a second gate on
    it, and un-parking would then resolve one and leave the other holding the epic.
    """
    epic = store.show(epic_id)
    if epic is None:
        raise TrackerError(f"park: {epic_id} is not a record the tracker knows")
    if epic.status == BLOCKED:
        gate = PARKED.search(epic.notes or "")
        held = f" (gate {gate.group('gate')})" if gate else ""
        raise TrackerError(
            f"park: {epic_id} is already blocked{held} — `unpark` it first; a second park "
            f"would stack a second gate"
        )
    gate_id = store.gate_create(epic_id, reason)
    store.note(epic_id, f"PARKED {gate_id}: {reason}")
    store.update(epic_id, status=BLOCKED)
    return gate_id


def unpark(store: TaskStore, epic_id: str, gate_id: str | None = None) -> str:
    """The mirror of `park`: resolve the gate AND reopen the epic, as one operation.

    The gate is the one `park` recorded on the epic (the last `PARKED <gate>:` note), or
    the one the caller names. On a backend that records the target on the gate (mdfiles)
    the open gate whose `depends_on` names the epic is a third source. Ambiguity —
    nothing recorded, nothing named, more than one gate holding it — REFUSES rather than
    guessing: resolving the wrong gate reopens nothing, and un-parking with a gate still
    open leaves the epic blocked with no note saying why.
    """
    epic = store.show(epic_id)
    if epic is None:
        raise TrackerError(f"unpark: {epic_id} is not a record the tracker knows")
    open_gates = {g.id: g for g in store.gate_list()}
    if gate_id is None:
        recorded = PARKED.findall(epic.notes or "")
        candidates = [g for g in recorded if g in open_gates]
        if not candidates:
            candidates = [g.id for g in open_gates.values() if epic_id in g.depends_on]
        if len(set(candidates)) > 1:
            raise TrackerError(
                f"unpark: {epic_id} has more than one open gate ({', '.join(sorted(set(candidates)))}); "
                f"pass --gate <id> to say which"
            )
        gate_id = candidates[-1] if candidates else None
    if gate_id is None:
        if epic.status != BLOCKED:
            raise TrackerError(f"unpark: {epic_id} is {epic.status}, not blocked, and holds no open gate")
        raise TrackerError(
            f"unpark: no gate recorded for {epic_id} (no `PARKED <gate>:` note and no open gate "
            f"names it) — pass --gate <id>"
        )
    if gate_id not in open_gates:
        raise TrackerError(f"unpark: gate {gate_id} is not an open gate")
    store.gate_resolve(gate_id)
    store.update(epic_id, status=OPEN)
    store.note(epic_id, f"UNPARKED {gate_id}")
    return gate_id


@runtime_checkable
class MemoryStore(Protocol):
    """Durable cross-task insight. A different subsystem from the tracker it ships with."""

    def remember(self, text: str, *, key: str | None = None) -> None: ...

    def recall(self, query: str) -> list[str]: ...

    def memories(self) -> list[str]: ...


@dataclass(frozen=True)
class ClaimResult:
    """Whether this actor holds the task, and who holds it if not."""

    held: bool
    holder: str = ""
    #: True when this actor already held it — re-claiming is idempotent for one actor,
    #: which is what lets a resumed run re-enter its own task cleanly.
    reentrant: bool = False


@dataclass(frozen=True)
class SlotState:
    free: bool
    holder: str = ""
    #: Set when the recorded holder's process is gone. A stale lease may be stolen, and
    #: the steal is recorded rather than silent.
    stale: bool = False


@runtime_checkable
class Coordination(Protocol):
    """One machine's worker mutexes. ONE implementation, shared by every backend."""

    def try_claim(self, task_id: str, actor: str) -> ClaimResult: ...

    def release_claim(self, task_id: str, actor: str) -> bool: ...

    def slot_check(self) -> SlotState: ...

    def slot_acquire(self, holder: str) -> bool: ...

    def slot_release(self, holder: str, *, force: bool = False) -> bool: ...


@dataclass(frozen=True)
class Event:
    category: str
    target: str
    payload: dict[str, Any]
    at: str = ""


@runtime_checkable
class Telemetry(Protocol):
    """The cost and health series. ONE implementation, shared by every backend."""

    def record(self, category: str, target: str, payload: dict[str, Any]) -> bool:
        """Append one event. MUST NOT raise: telemetry never fails real work."""
        ...

    def read(self, category: str | None = None) -> list[Event]: ...
