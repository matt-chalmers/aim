"""The `bd` backend: a near-passthrough adapter over the beads CLI.

WHY THIS IS THIN ON PURPOSE. `bd` already provides everything `TaskStore` asks for, so
this file's job is translation, not logic. The one thing it genuinely OWNS is the result
normalisation that was copy-pasted into six call sites — `bd list --all --json` returns a
bare list on some paths and `{"issues": [...]}` on others, and `bd list` and `bd show`
disagree about how a dependency edge is shaped. Every one of those readers had to know
both, and any of them could have been written to know only one and report clean forever.

WHAT IS NOT PASSED THROUGH, AND WHY.

  * `validate` is computed by `tracker.graph`, not parsed out of `bd swarm validate`.
    That command prints for humans; scraping it would make the harness's scheduling
    depend on a text format nobody promised to keep. The graph walk answers the same
    question from records both backends already expose.
  * `ready` IS passed through, because bd applies gate exclusion natively and it is the
    authority on its own queue. The shared graph is what the markdown backend will use.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from . import graph
from .port import (
    CLOSED,
    GATE,
    TASK,
    Capabilities,
    Event,
    Task,
    TrackerError,
    Validation,
)

#: beads refuses a record past roughly this size, and it fails CLOSED with no warning: a
#: 38-character append fails exactly as a 3KB one does. Declared so callers read it from
#: capabilities rather than hardcoding a constant that is false for other backends.
RECORD_CEILING = 64_000

TIMEOUT = 60


def _run(args: list[str], *, cwd: str | None = None, timeout: int = TIMEOUT):
    try:
        return subprocess.run(
            ["bd", *args], capture_output=True, text=True, cwd=cwd, timeout=timeout
        )
    except FileNotFoundError as exc:
        raise TrackerError("`bd` is not on PATH; the beads backend needs it") from exc
    except subprocess.SubprocessError as exc:
        raise TrackerError(f"bd {' '.join(args)} failed: {exc}") from exc


def _ok(args: list[str], cwd: str | None) -> subprocess.CompletedProcess:
    """Run `bd`, and REFUSE TO TREAT A FAILURE AS AN EMPTY RESULT.

    `bd` invoked outside a workspace exits 1 with "no beads database found" on stderr
    and nothing on stdout. Read as JSON that is `[]`, exit 0 — the same answer as a
    genuinely empty backlog. An unattended campaign took that answer from a
    mis-resolved repository, concluded the epic queue was exhausted, and reported a
    clean zero-work run against 17 open epics. The empty list and the missing database
    must never be the same value.
    """
    proc = _run(args, cwd=cwd)
    if proc.returncode != 0:
        raise _failure(args, proc, cwd)
    return proc


def _failure(args: list[str], proc: subprocess.CompletedProcess, cwd: str | None) -> TrackerError:
    detail = (proc.stderr or proc.stdout).strip().splitlines()
    return TrackerError(
        f"bd {' '.join(args)} failed (exit {proc.returncode}) in "
        f"{cwd or 'the current directory'}: {detail[0] if detail else 'no output'}"
    )


def _issues(raw: str) -> list[dict]:
    """THE normalisation, in one place.

    `bd list --all --json` yields a bare list on some paths and `{"issues": [...]}` on
    others; `bd show --json` yields a single object or a one-element list. Six readers
    each carried their own version of this, and a reader that knew only one shape would
    have returned nothing and reported clean.
    """
    if not raw.strip():
        return []
    try:
        d = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TrackerError("bd did not return JSON") from exc
    if isinstance(d, list):
        return [x for x in d if isinstance(x, dict)]
    if isinstance(d, dict):
        inner = d.get("issues")
        if isinstance(inner, list):
            return [x for x in inner if isinstance(x, dict)]
        # An ERROR PAYLOAD IS NOT A RECORD. `bd show <unknown> --json` exits non-zero and
        # returns {"error": ..., "schema_version": ...}; treating that as an issue built a
        # Task with an empty id, so a lookup for something absent came back looking real.
        if "error" in d and "id" not in d:
            return []
        return [d]
    return []


def _edges(d: dict) -> tuple[str, ...]:
    """Dependency ids, accepting both shapes bd emits.

    `bd list --all --json` shapes an edge as `{"depends_on_id": ...}`; `bd show --json`
    shapes the same field as `{"id": ...}`. Reading only one silently returns no edges,
    which makes every dependency check pass.
    """
    out: list[str] = []
    for dep in d.get("dependencies") or []:
        if isinstance(dep, dict):
            # ONLY `blocks` EDGES. bd records the PARENT-CHILD relationship in the same
            # array, so reading every entry made each child appear to depend on its own
            # epic — which no wave can ever satisfy, because an epic closes after its
            # children. Wave levelling put both wave-1 tasks behind their parent, and the
            # rendered view showed the epic as their blocker.
            #
            # The field is `dependency_type` from `show` and `type` from `list`: the same
            # two-shape split as the id itself. Absent means an older record, and those
            # only ever held blocking edges.
            kind = str(dep.get("dependency_type") or dep.get("type") or "blocks")
            if kind != "blocks":
                continue
            v = dep.get("depends_on_id") or dep.get("id")
            if v:
                out.append(str(v))
        elif isinstance(dep, str):
            out.append(dep)
    return tuple(out)


def _task(d: dict) -> Task:
    return Task(
        id=str(d.get("id") or ""),
        type=str(d.get("issue_type") or d.get("type") or TASK),
        status=str(d.get("status") or ""),
        title=str(d.get("title") or ""),
        description=str(d.get("description") or ""),
        acceptance=str(d.get("acceptance_criteria") or ""),
        notes=str(d.get("notes") or ""),
        priority=d.get("priority") if isinstance(d.get("priority"), int) else None,
        parent=str(d["parent"]) if d.get("parent") else None,
        depends_on=_edges(d),
        labels=tuple(str(x) for x in (d.get("labels") or [])),
        assignee=str(d["assignee"]) if d.get("assignee") else None,
        created_at=str(d.get("created_at") or ""),
        updated_at=str(d.get("updated_at") or ""),
        close_reason=str(d.get("close_reason") or d.get("closed_reason") or ""),
        raw=d,
    )


def _first_id(raw: str, *, what: str) -> str:
    """The `id` from a `--json` write, or a clear failure.

    Write commands print for humans — a tick, an em-dash, a tip about installing a
    plugin. Scraping an id out of that is one CLI polish away from returning the wrong
    token, and a caller that records the wrong id has corrupted the record it just made.
    """
    rows = _issues(raw)
    if rows and rows[0].get("id"):
        return str(rows[0]["id"])
    raise TrackerError(f"{what} did not return an id: {raw.strip()[:200]!r}")


class BeadsTaskStore:
    """`TaskStore` over the beads CLI."""

    def __init__(self, cwd: str | None = None) -> None:
        self.cwd = cwd

    def capabilities(self) -> Capabilities:
        return Capabilities(
            name="beads",
            record_bytes=RECORD_CEILING,
            molecules=True,
            prime=True,
            tracked_export=True,
            # The store, its config and the exported issues.jsonl all live here.
            owned_paths=(".beads/",),
        )

    # --- reads ---------------------------------------------------------------

    def _json(self, args: list[str]) -> list[dict]:
        return _issues(_ok(args, self.cwd).stdout)

    def show(self, task_id: str) -> Task | None:
        proc = _run(["show", task_id, "--json"], cwd=self.cwd)
        # An unknown id exits non-zero WITH an error payload on stdout, and that is
        # None. Non-zero with nothing on stdout is bd itself failing — no workspace,
        # most likely — and "not found" would be a lie about a database never opened.
        if proc.returncode != 0 and not proc.stdout.strip():
            raise _failure(["show", task_id, "--json"], proc, self.cwd)
        rows = _issues(proc.stdout) if proc.returncode == 0 else []
        return _task(rows[0]) if rows and rows[0].get("id") else None

    def list(
        self,
        *,
        type: str | None = None,
        status: str | None = None,
        parent: str | None = None,
        limit: int | None = None,
    ) -> list[Task]:
        args = ["list"]
        if type:
            args += ["--type", type]
        if status:
            args += ["--status", status]
        else:
            args += ["--all"]
        if parent:
            args += ["--parent", parent]
        if limit:
            args += ["--limit", str(limit)]
        args += ["--json"]
        return [_task(d) for d in self._json(args)]

    def ready(
        self, *, parent: str | None = None, limit: int | None = None
    ) -> list[Task]:
        args = ["ready"]
        if parent:
            args += ["--parent", parent]
        args += ["--limit", str(limit or 500), "--json"]
        return [_task(d) for d in self._json(args)]

    def validate(self, epic_id: str) -> Validation:
        return graph.validate(self.list(parent=epic_id))

    # --- writes --------------------------------------------------------------

    def _must(self, args: list[str]) -> str:
        proc = _run(args, cwd=self.cwd)
        if proc.returncode != 0:
            raise TrackerError(
                f"bd {' '.join(args[:2])} failed: {(proc.stderr or proc.stdout).strip()[:300]}"
            )
        return proc.stdout

    def create(
        self,
        title: str,
        *,
        type: str = TASK,
        description: str = "",
        priority: int | None = None,
        parent: str | None = None,
        labels: tuple[str, ...] = (),
    ) -> str:
        args = ["create", title, "-t", type]
        if priority is not None:
            args += ["-p", str(priority)]
        if description:
            args += ["--description", description]
        if parent:
            args += ["--parent", parent]
        for lab in labels:
            args += ["--label", lab]
        # `--json` and NOT the human output. `bd create` prints
        # "✓ Created issue: ws-1rz — second" plus tips, and scraping an id out of that
        # worked only by luck: the em-dash separator and the tip text are one CLI polish
        # away from producing a different "first token containing a hyphen".
        return _first_id(self._must([*args, "--json"]), what="create")

    def update(self, task_id: str, **fields: Any) -> None:
        args = ["update", task_id]
        for key, value in fields.items():
            if value is None or value is False:
                continue
            flag = f"--{key.replace('_', '-')}"
            args += [flag] if value is True else [flag, str(value)]
        self._must(args)

    def close(self, task_id: str, reason: str) -> None:
        # `--reason` is required; the positional form `bd close <id> "msg"` errors.
        self._must(["close", task_id, "--reason", reason])

    def note(self, task_id: str, text: str) -> None:
        # `--append-notes`, never `--design`: that field is write-only and never reaches
        # a reader.
        self._must(["update", task_id, "--append-notes", text])

    def dep_add(self, dependent: str, blocker: str) -> None:
        self._must(["dep", "add", dependent, blocker])

    def supersede(self, old_id: str, new_id: str) -> None:
        self._must(["supersede", old_id, "--with", new_id])

    def delete(self, task_id: str) -> None:
        """Delete outright. `--force` IS REQUIRED and its absence is silent.

        Without it `bd delete` prints a DELETE PREVIEW, deletes nothing, and exits ZERO —
        so the adapter reported success while the record survived. A destructive verb that
        no-ops while claiming to have worked is worse than one that fails.

        The caller is expected to have chosen this over `supersede` deliberately: the
        planner's rule is to prefer superseding wherever there is history worth keeping,
        and delete is for records that are simply wrong with nothing to preserve.
        """
        self._must(["delete", task_id, "--force"])

    # --- gates ---------------------------------------------------------------

    def gate_create(self, blocks: str, reason: str) -> str:
        """Create a human gate against `blocks`.

        BD REPORTS FAILURE AND CREATES THE GATE ANYWAY, and that is not a bug to route
        around — it is behaviour the campaign loop already documents: `bd gate create
        --blocks <epic>` exits non-zero with *"epics can only block other epics, not
        tasks"* because it refuses the blocking EDGE, while the gate ISSUE is written.
        Verified against bd 1.0.3.

        So a non-zero exit is not conclusive here. The gate list is diffed across the
        call rather than trusted from the return, which is deterministic where scraping a
        partial error payload is not.

        The caller still owes the other half — `update(<epic>, status="blocked")` — since
        the missing edge means the gate alone does NOT remove the epic from the queue.
        """
        before = {g.id for g in self.gate_list()}
        args = [
            "gate", "create", "--type=human",
            "--blocks", blocks, "--reason", reason, "--json",
        ]
        proc = _run(args, cwd=self.cwd)
        if proc.returncode == 0:
            try:
                return _first_id(proc.stdout, what="gate create")
            except TrackerError:
                pass
        made = [g.id for g in self.gate_list() if g.id not in before]
        if made:
            return made[0]
        raise TrackerError(
            f"gate create failed and no gate appeared: "
            f"{(proc.stderr or proc.stdout).strip()[:200]}"
        )

    def gate_list(self) -> list[Task]:
        rows = self._json(["list", "--type", GATE, "--all", "--json"])
        return [_task(d) for d in rows if d.get("status") != CLOSED]

    def gate_resolve(self, gate_id: str) -> None:
        self._must(["gate", "resolve", gate_id])

    # --- the tracked artefact ------------------------------------------------

    def export(self, path: str = ".beads/issues.jsonl") -> None:
        """Regenerate the git-tracked jsonl.

        `-o` IS LOAD-BEARING. Plain `bd export` streams to stdout and writes nothing, so
        omitting it publishes a backlog that disagrees with the code while `git status`
        looks clean.
        """
        self._must(["export", "-o", path])

    def autosync(self, enabled: bool) -> None:
        """`export.auto`, which stages issues.jsonl into whatever commit comes next.

        A wave disables it at pre-flight precisely so a worker's tracker write does not
        land in a sibling's commit, and restores it at close. Forgetting the restore
        leaves `bd close` no longer keeping the tracked jsonl fresh.
        """
        self._must(["config", "set", "export.auto", "true" if enabled else "false"])

    def prime(self) -> str:
        return _ok(["prime"], self.cwd).stdout

    def label(self, task_id: str, name: str, *, remove: bool = False) -> None:
        self._must(["label", "remove" if remove else "add", task_id, name])


class BeadsMemoryStore:
    """`MemoryStore` over `bd remember` / `recall` / `memories`."""

    def __init__(self, cwd: str | None = None) -> None:
        self.cwd = cwd

    def remember(self, text: str, *, key: str | None = None) -> None:
        args = ["remember", text]
        if key:
            args += ["--key", key]
        proc = _run(args, cwd=self.cwd)
        if proc.returncode != 0:
            raise TrackerError(f"bd remember failed: {proc.stderr.strip()[:200]}")

    def recall(self, query: str) -> list[str]:
        return [ln for ln in _ok(["recall", query], self.cwd).stdout.splitlines() if ln.strip()]

    def memories(self) -> list[str]:
        return [ln for ln in _ok(["memories"], self.cwd).stdout.splitlines() if ln.strip()]


def legacy_events(cwd: str | None = None) -> list[Event]:
    """Historical `event` tasks, for `LocalTelemetry`'s legacy bridge.

    Read-only and best-effort: this exists so moving telemetry out of the tracker costs
    no history and needs no import step. When bd is gone, it is simply not supplied.
    """
    try:
        rows = _issues(_run(["list", "--all", "--json"], cwd=cwd).stdout)
    except TrackerError:
        return []
    out: list[Event] = []
    for d in rows:
        if d.get("issue_type") != "event":
            continue
        try:
            payload = json.loads(d.get("payload") or "{}")
        except json.JSONDecodeError:
            continue
        out.append(
            Event(
                category=str(d.get("event_kind") or ""),
                target=str(d.get("event_target") or d.get("id") or ""),
                payload=payload,
                at=str(d.get("created_at") or "")[:19],
            )
        )
    return out
