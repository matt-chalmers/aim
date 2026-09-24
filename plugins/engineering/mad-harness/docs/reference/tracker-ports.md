# Tracker ports

Four ports in [`harness/tracker/port.py`](../../harness/tracker/port.py). Only one of them
is per-backend, and that split is the design's main idea.

| port | per backend? | covers |
|---|---|---|
| `TaskStore` | **yes** | records, the graph, gates, export |
| `Coordination` | **no — one local implementation** | claims, the merge slot |
| `Telemetry` | **no — one local implementation** | the append-only event series |
| `MemoryStore` | **yes** | remember / recall |

**Why only `TaskStore` varies.** Every worker runs on one machine against one filesystem —
eight worktrees, one POSIX namespace. So claiming a task and holding the merge slot
coordinate *this machine's processes* and have nothing to do with where records are stored.
One implementation each, serving every backend, instead of one per backend — which is the
change that becomes expensive once two adapters have each baked in their own claim logic.

## Types

```python
@dataclass(frozen=True)
class Task:
    id: str; type: str; status: str
    title: str = ""; description: str = ""; notes: str = ""
    priority: int | None = None
    parent: str | None = None
    depends_on: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    assignee: str | None = None
    created_at: str = ""; updated_at: str = ""
    acceptance: str = ""              # first-class: what the verifier checks against; writable via update()
    close_reason: str = ""            # first-class: callers need it, `raw` is off limits
    raw: dict[str, Any] = field(default_factory=dict)   # DIAGNOSTICS ONLY

@dataclass(frozen=True)
class Capabilities:
    name: str
    record_bytes: int | None = None   # None = no ceiling
    molecules: bool = False
    prime: bool = False
    tracked_export: bool = True
    owned_paths: tuple[str, ...] = () # repo-relative prefixes this backend writes
    export_path: str | None = None    # the one artefact `export()` writes — what a close-out commits

@dataclass(frozen=True)
class Validation:
    ok: bool
    waves: tuple[Wave, ...]           # Wave(index, task_ids)
    cycles: tuple[tuple[str, ...], ...] = ()
    orphans: tuple[str, ...] = ()

@dataclass(frozen=True)
class ClaimResult:
    held: bool; holder: str = ""; reentrant: bool = False

@dataclass(frozen=True)
class SlotState:
    free: bool; holder: str = ""; stale: bool = False

@dataclass(frozen=True)
class Event:
    category: str; target: str; payload: dict[str, Any]; at: str = ""
```

## `TaskStore`

```python
capabilities() -> Capabilities
show(task_id) -> Task | None          # resolves CLOSED records too
list(*, type=, status=, parent=, limit=) -> list[Task]
ready(*, parent=, limit=) -> list[Task]
validate(epic_id) -> Validation       # levelling, cycles, orphans
create(title, *, type=, description=, ...) -> str
update(task_id, **fields) / close(task_id, reason) / note(task_id, text)
dep_add(dependent, blocker) / supersede(old, new) / delete(task_id)
gate_create(blocks, reason) / gate_list() / gate_resolve(gate_id)
export() / autosync(enabled)
```

## Contracts that are easy to get wrong

**`show` must resolve a closed record.** If it returned `None`, "dependency satisfied"
would be indistinguishable from "dependency missing", and the scheduler would have to
guess. This is why the markdown backend's archive tier has a `show` fallback.

**`close` must record the reason, not merely the status.** One backend accepted the reason
and dropped it, silently, until a test looked. `Task.close_reason` is a first-class field
for the same reason: a worker resuming after an operator refused its request needs to be
told *why*, and reading `raw` is forbidden.

**`raw` is for diagnostics only.** Nothing in the harness may branch on it, or the
abstraction is decorative.

**Capabilities are declared, never inferred.** Including `owned_paths` — the paths a backend
writes — because every consumer that must skip them would otherwise hardcode `.beads/` and
break under the next backend.

**Every record type must round-trip on every backend.** One backend validates its type
vocabulary and rejects unknown ones. A type that works on one and fails on the other is a
silent one-backend feature.

## `Coordination` and `Telemetry` — one implementation each

```python
class Coordination(Protocol):
    def try_claim(self, task_id: str, actor: str) -> ClaimResult: ...
    def release_claim(self, task_id: str, actor: str) -> bool: ...
    def slot_check(self) -> SlotState: ...
    def slot_acquire(self, holder: str) -> bool: ...
    def slot_release(self, holder: str) -> bool: ...

class Telemetry(Protocol):
    def record(self, category: str, target: str, payload: dict) -> bool: ...
    def events(self, category: str | None = None) -> list[Event]: ...
```

| behaviour | guarantee |
|---|---|
| contested claim | exactly one caller gets `held=True`; losers get the holder's name |
| same actor re-claiming | `held=True, reentrant=True` — idempotent re-dispatch after `/halt` |
| merge slot with a dead holder | `stale=True`; the steal is **logged**, not silent |
| `record()` failure | returns `False`; never raises — telemetry must not fail a dispatch |

## Capability gating

Capabilities are declared, never inferred, and callers branch on them rather than on the
backend's name.

| capability | absent means |
|---|---|
| `record_bytes` | no size ceiling — every guard that exists because one backend write-locks past ~64KB reports itself inert rather than warning about nothing |
| `molecules` | `swarm create` is unavailable; the caller falls back rather than failing |
| `prime` | no curated workflow dump |
| `owned_paths` | — always present; consumers that must skip the tracker's own files ask instead of hardcoding `.beads/` |
| `export_path` | no tracked artefact to commit — `close-epic.sh` skips its commit step and says so. Narrower than `owned_paths`: that prefix also holds config the autosync toggle rewrites |

## Switching backends

```bash
tk.sh migrate --to mdfiles --dry-run    # how many records would move
tk.sh migrate --to mdfiles              # move them
```

**Not a port method.** An `import` verb would have each backend parse its own export
format, which migrates nothing — a JSONL export means nothing to the markdown backend.
Migration is a read through one port and a write through another, so it needs no new
interface and works for any pair satisfying the contract, including backends not written
yet.

**Ids are not preserved, because they cannot be.** Each backend mints its own (`t-a1b2c3`
against `PROJ-4f2a.1`), so every edge is rewritten through a map built while creating —
which is the whole reason this is more than a loop.

| preserved | not preserved |
|---|---|
| type, title, description, priority, labels | ids |
| parent and dependency edges | timestamps |
| status, and a closed record's `close_reason` | backend-specific `raw` fields |

Order matters and is not arbitrary: parents before children so `parent` can be set at
creation, **all** edges after **all** creates so the map is complete, and closes last so a
record's dependencies exist when it is closed.

**The source is never modified.** A reported failure is recoverable — fix the record and
re-run into a clean target. A partial migration that looked complete is the outcome this
avoids, which is why anything skipped is named rather than counted.

## Cross-machine coordination

Claims and the merge slot live in `.harness/run/`, local to a checkout. That is right for
the workers of one campaign and no protection between machines: two campaigns can claim the
same task, both merge, and the tracked export conflicts on push or silently takes the last
write.

An **epic lease** on a git ref closes that. The remote is the only thing two machines
already share, so it needs no new infrastructure.

```bash
tk.sh lease list              # every leased epic, and who holds it
tk.sh lease acquire <epic>    # exits non-zero if another machine holds it
tk.sh lease release <epic>    # idempotent
tk.sh lease steal <epic>      # only past the TTL; the steal is recorded on the remote
```

Measured against a real remote before being relied on, because the atomicity belongs to the
remote's ref update rather than to git:

| | |
|---|---|
| `push <obj>:refs/harness/epic-lease/e1` | `[new reference]`, exit 0 |
| the same from a second machine | rejected, **exit 1**, holder unchanged |
| `push :refs/harness/epic-lease/e1` | released, exit 0 |

**Epic granularity, not repository.** `campaign-loop` already runs one epic at a time, so
the lease matches the execution model and lets two machines work different epics — more
useful than a global lock, simpler than per-task locking.

**An empty source refspec is a delete.** A failed `commit-tree` would produce
`push origin :refs/...` and remove another machine's lease. `acquire()` raises rather than
pushing without an object, and a test pins it.

### What this does not solve

| | |
|---|---|
| the tracked export still merges across machines | disjoint epics make conflicts rare, not impossible |
| cross-epic dependency edges go stale | `ready()` sees the other machine's state as of the last pull; pulling first bounds it |
| two campaigns in ONE checkout | unaffected — that is what the dirty-tree check at pre-flight is for |

## The conformance contract

One test module, parameterised over backends, run against the **real** binaries.

```bash
make conformance     # the whole contract x every shipped backend
```

Written *before* the second backend existed, deliberately: a suite written afterwards
documents whatever the second implementation already does; written first, it is the
specification the second implementation has to meet.

Mocks were rejected for a measured reason — two assumptions about `bd` were wrong the first
time they met the real CLI: `create` printed its id inside human prose, and `list` omits
`dependencies` entirely until an edge exists.

## The differential

The contract proves both backends obey every rule someone thought to write down.
[`harness/wavelab/`](../../harness/wavelab/) asks the question it cannot: given the same
base and the same epic, did real agents end up in the same place?

```bash
harness/wavelab/reset.sh && harness/wavelab/compare.sh
```
