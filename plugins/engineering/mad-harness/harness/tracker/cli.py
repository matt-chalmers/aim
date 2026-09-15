"""`tk.sh` — the one command prompts call instead of naming a tracker.

WHY A SHIM AND NOT A LIBRARY CALL. Prompts are markdown read by a model; they cannot
import Python. Today 95 operational sites say `bd <verb>` outright, which is what makes
the backend unswappable. One shim with a bd-shaped verb vocabulary turns that migration
into a mechanical substitution and keeps the doctrine's shape intact.

EVERY VERB ANSWERS. A verb a backend cannot serve raises `NotSupported` and exits
non-zero, never returns empty — the same rule `scan.sh` and `peek.sh` hold, and for the
same reason: an empty answer that means "not implemented" reads as "searched and found
nothing".
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict

import tracker
from tracker.port import NotSupported, Task, TrackerError


def _actor(explicit: str | None) -> str:
    """Who is claiming. Explicit wins; otherwise the per-worker identity from the env.

    REFUSES rather than inventing one. A claim recorded against a guessed identity is a
    mutex nobody can release: the real holder's release is rejected because the names do
    not match, and the task stays locked until the lease ages out.
    """
    import getpass

    for src in (explicit, os.environ.get("TRACKER_ACTOR"), os.environ.get("BEADS_ACTOR")):
        if src:
            return src
    try:
        return f"user-{getpass.getuser()}"
    except Exception as exc:  # noqa: BLE001
        raise TrackerError(
            "no actor: pass --actor, or export TRACKER_ACTOR (a worker gets one from "
            "`.swarm-env`)"
        ) from exc


def _emit(obj, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, default=str))
        return
    if isinstance(obj, list):
        for item in obj:
            print(item)
    else:
        print(obj)


def _rows(tasks: list[Task], as_json: bool) -> None:
    if as_json:
        print(json.dumps([asdict(t) for t in tasks], default=str))
        return
    for t in tasks:
        print(f"{t.id:<20} {t.status:<12} {t.type:<9} {t.title}")


def _record(t: Task) -> str:
    """ONE record, WHOLE. `show` used to print the same one-line row `list` does — id,
    status, type, title — and nothing else. Eighteen prompt sites read a task through
    `tk.sh show <id>` and tell the agent to expect "description, acceptance criteria,
    dependencies, notes"; every worker built, and every verifier judged, against a title.
    The row is for scanning a list. A record is for reading.
    """
    out = [f"{t.id}  {t.status}  {t.type}  {t.title}".rstrip()]
    meta = []
    if t.priority is not None:
        meta.append(f"priority: {t.priority}")
    if t.parent:
        meta.append(f"parent: {t.parent}")
    if t.assignee:
        meta.append(f"assignee: {t.assignee}")
    if t.labels:
        meta.append(f"labels: {', '.join(t.labels)}")
    if meta:
        out.append("  ".join(meta))
    out.append(f"depends_on: {', '.join(t.depends_on) if t.depends_on else '(none)'}")
    if t.created_at or t.updated_at:
        out.append(f"created: {t.created_at or '?'}  updated: {t.updated_at or '?'}")
    out.append("")
    out.append(t.description.rstrip() if t.description.strip() else "(no description)")
    if t.notes.strip():
        out += ["", "--- notes ---", t.notes.rstrip()]
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    # `--json` and `--backend` are declared on a PARENT parser so they are accepted
    # AFTER the verb: prompts write `tk.sh ready --json`, mirroring `bd ready --json`,
    # and a global-only flag would have forced every one of the 95 call sites to reorder.
    # `default=SUPPRESS` IS LOAD-BEARING, not tidiness. With an ordinary default, a
    # subparser sharing a parent's argument OVERWRITES the value the main parser already
    # parsed — so `tk.sh --json ready` silently produced human output while
    # `tk.sh ready --json` worked. Silently, because a flag that is dropped rather than
    # rejected leaves the caller parsing prose as if it were JSON. With SUPPRESS the
    # attribute is set only where it was actually given, and the two positions agree.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="machine-readable output",
    )
    common.add_argument(
        "--backend",
        default=argparse.SUPPRESS,
        help="override the configured backend",
    )
    # A READ-ONLY LENS CANNOT BE TRUSTED TO ONLY READ. `bd --readonly` gave tasks this and
    # nothing gave it to any other backend, so the guarantee evaporated the moment the
    # tracker changed. Enforced here instead: the shim refuses write verbs outright, for
    # every backend, before a call is made.
    common.add_argument(
        "--readonly",
        action="store_true",
        default=argparse.SUPPRESS,
        help="refuse every write verb; for lenses and analysts, which must not mutate",
    )

    ap = argparse.ArgumentParser(
        prog="tk.sh",
        parents=[common],
        description="The harness's tracker, whichever backend this project declares.",
    )
    sub = ap.add_subparsers(dest="verb", required=True)

    def add(name, **kw):
        return sub.add_parser(name, parents=[common], **kw)

    add("backend", help="print the active backend and its capabilities")

    p = add("lease", help="epic leases across machines — acquire | release | list | show")
    p.add_argument("action", choices=["acquire", "release", "list", "show", "steal"])
    p.add_argument("epic", nargs="?")
    p.add_argument("--holder", help="defaults to TRACKER_ACTOR, then USER")
    p.add_argument("--ttl", type=int, help="seconds before a lease is reclaimable")

    p = add("migrate", help="copy every record into another backend")
    p.add_argument("--to", required=True, help="target backend, e.g. mdfiles")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would move without writing anything",
    )

    p = add("show", help="one record, including closed ones")
    p.add_argument("id")

    p = add("list", help="records matching a filter")
    p.add_argument("--type")
    p.add_argument("--status")
    p.add_argument("--parent")
    p.add_argument("--limit", type=int)

    p = add("ready", help="dispatchable now")
    p.add_argument("--parent")
    p.add_argument("--limit", type=int)

    p = add("validate", help="wave levelling, cycles and orphans for an epic")
    p.add_argument("epic")

    p = add("create")
    p.add_argument("title")
    p.add_argument("-t", "--type", default="task")
    p.add_argument("-p", "--priority", type=int)
    p.add_argument("--description", default="")
    p.add_argument("--parent")

    p = add("close")
    p.add_argument("id")
    p.add_argument("--reason", required=True)

    p = add("note", help="append to the audit trail; never replaces")
    p.add_argument("id")
    p.add_argument("text")

    p = add("dep", help="add a dependency edge")
    p.add_argument("dependent")
    p.add_argument("blocker")

    p = add("update", help="set fields on a record")
    p.add_argument("id")
    p.add_argument("--status")
    p.add_argument("--title")
    p.add_argument("--description")
    p.add_argument("--priority", type=int)
    p.add_argument("--parent")
    p.add_argument("--assignee")
    # `--append-notes` is spelled as bd spells it, so the 95 migrated call sites are a
    # substitution rather than a rewrite.
    p.add_argument("--append-notes", dest="append_notes")

    p = add("supersede", help="close a record, pointing at its replacement")
    p.add_argument("old")
    p.add_argument("--with", dest="new", required=True)

    p = add("delete", help="remove a record outright; prefer supersede where there is history")
    p.add_argument("id")

    p = add("label", help="add or remove one label")
    p.add_argument("action", choices=["add", "remove"])
    p.add_argument("id")
    p.add_argument("name")

    p = add("gate", help="human gates: create, list, resolve")
    p.add_argument("action", choices=["create", "list", "resolve"])
    p.add_argument("target", nargs="?", help="the id to block, or the gate to resolve")
    p.add_argument("--reason", default="")

    p = add(
        "autosync",
        help="whether the backend may write its tracked artefact unasked (a wave "
        "disables this at pre-flight and restores it at close)",
    )
    p.add_argument("state", choices=["on", "off"])

    add("prime", help="the backend's curated context dump, if it has one")

    p = add("render", help="a readable view of one epic's plan and state")
    p.add_argument("epic")
    p.add_argument("--write", dest="dest", default=None, help="write to this path")
    p.add_argument(
        "--check",
        action="store_true",
        help="fail if the file disagrees with the tracker; it is generated",
    )

    # `--actor` and `--holder` DEFAULT FROM THE ENVIRONMENT, because a worker already has
    # one: `.swarm-env` exports it per worker, and requiring it in every prompt would put
    # the same shell interpolation into 95 call sites for no gain.
    p = add("claim", help="take a task; the guard against double-dispatch")
    p.add_argument("id")
    p.add_argument("--actor", default=None)

    p = add("release", help="give a claim back")
    p.add_argument("id")
    p.add_argument("--actor", default=None)

    add("slot-check", help="is the merge slot free?")
    p = add("slot-acquire")
    p.add_argument("--holder", default=None)
    p = add("slot-release")
    p.add_argument("--holder", default=None)
    p.add_argument("--force", action="store_true")

    p = add("event", help="record one telemetry event")
    p.add_argument("category")
    p.add_argument("target")
    p.add_argument("payload", help="JSON object")

    p = add("events", help="read the telemetry series back")
    p.add_argument("--category")

    p = add("export", help="regenerate the tracked artefact (one actor)")
    p.add_argument("--out", default=None)

    add("memories")
    p = add("remember")
    p.add_argument("text")
    p.add_argument("--key")
    p = add("recall")
    p.add_argument("query")

    add(
        "foreign-claims",
        help="claims recorded by another host — the cross-machine collision warning",
    )
    return ap


#: Verbs that mutate. `--readonly` refuses these; everything else is a read.
WRITE_VERBS = frozenset({
    "create", "close", "note", "dep", "update", "supersede", "delete", "label",
    "gate", "export", "autosync", "remember", "claim", "release",
    "slot-acquire", "slot-release", "event",
    # It writes to the TARGET store. The source is never modified, but a verb that
    # creates records anywhere is a write, and --readonly must refuse it.
    "migrate",
    # acquire/release/steal write a ref on the REMOTE; list/show do not, but the verb is
    # classified as a whole and refusing all of it under --readonly is the safe side.
    "lease",
})


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    v = args.verb
    # `render` reads the tracker but `--write` puts a file in the corpus. A read-only
    # agent may look at the view; it may not generate one into the repository it is
    # judging.
    if getattr(args, "readonly", False) and v == "render" and getattr(args, "dest", None):
        print(
            "REFUSED: `render --write` writes into the repository, and --readonly was "
            "given. Drop --write to print the view instead.",
            file=sys.stderr,
        )
        return 4
    if getattr(args, "readonly", False) and v in WRITE_VERBS:
        print(
            f"REFUSED: `{v}` writes, and --readonly was given. A read-only agent that "
            f"mutates the tracker is no longer independent of the work it is judging.",
            file=sys.stderr,
        )
        return 4
    try:
        if v in ("claim", "release", "slot-check", "slot-acquire", "slot-release",
                 "foreign-claims"):
            co = tracker.coordination()
            if v == "claim":
                r = co.try_claim(args.id, _actor(args.actor))
                _emit(asdict(r), getattr(args, 'json', False))
                return 0 if r.held else 1
            if v == "release":
                return 0 if co.release_claim(args.id, _actor(args.actor)) else 1
            if v == "slot-check":
                _emit(asdict(co.slot_check()), getattr(args, 'json', False))
                return 0
            if v == "slot-acquire":
                return 0 if co.slot_acquire(_actor(args.holder)) else 1
            if v == "slot-release":
                return 0 if co.slot_release(_actor(args.holder), force=args.force) else 1
            _emit(co.foreign_claims(), getattr(args, 'json', False))
            return 0

        if v in ("event", "events"):
            tel = tracker.telemetry()
            if v == "event":
                ok = tel.record(args.category, args.target, json.loads(args.payload))
                return 0 if ok else 1
            _emit([asdict(e) for e in tel.read(args.category)], True)
            return 0

        if v in ("memories", "remember", "recall"):
            mem = tracker.memory_store(getattr(args, 'backend', None))
            if v == "memories":
                _emit(mem.memories(), getattr(args, 'json', False))
            elif v == "remember":
                mem.remember(args.text, key=args.key)
            else:
                _emit(mem.recall(args.query), getattr(args, 'json', False))
            return 0

        store = tracker.task_store(getattr(args, 'backend', None))
        if v == "lease":
            from models.resolve import REPO

            from . import lease as L

            # EVERY LEASE CALL RUNS GIT IN THE PROJECT. The wrapper cd's into the
            # harness before Python starts, so git without a cwd runs in the plugin's
            # own checkout — which has no `origin` — and every verb threw "cannot reach
            # the remote". Installed as a plugin, no epic was ever actually leased, and a
            # second machine saw a clear field. `REPO` honours MAD_HARNESS_REPO and the
            # caller's directory the same way every other tracker path does.
            repo = str(REPO)
            act = args.action
            if act == "list":
                current = L.held(cwd=repo)
                if not current:
                    print("no epics leased")
                    return 0
                for epic in sorted(current):
                    info = L.inspect(epic, cwd=repo)
                    print(f"  {info.describe()}" if info else f"  {epic}")
                return 0
            if not args.epic:
                print(f"{act} needs an epic id", file=sys.stderr)
                return 2
            if act == "show":
                info = L.inspect(args.epic, cwd=repo)
                print(info.describe() if info else f"{args.epic} is not leased")
                return 0
            if act == "acquire":
                got = L.acquire(args.epic, holder=args.holder, cwd=repo)
                if got:
                    print(f"acquired {args.epic}")
                    return 0
                other = L.inspect(args.epic, cwd=repo)
                print(
                    f"{args.epic} is already leased — {other.describe() if other else 'by another machine'}",
                    file=sys.stderr,
                )
                return 1
            if act == "steal":
                ttl = args.ttl if args.ttl is not None else L.DEFAULT_TTL_SECONDS
                got = L.steal(args.epic, ttl=ttl, holder=args.holder, cwd=repo)
                if got:
                    print(f"reclaimed {args.epic} — the steal is recorded on the remote")
                    return 0
                other = L.inspect(args.epic, cwd=repo)
                print(
                    f"refusing to steal {args.epic}: {other.describe() if other else 'not leased'}"
                    f" — not stale yet",
                    file=sys.stderr,
                )
                return 1
            print(f"released {args.epic}" if L.release(args.epic, cwd=repo) else f"could not release {args.epic}")
            return 0

        if v == "migrate":
            from .migrate import migrate as run_migration

            target_name = args.to
            if target_name == tracker.backend_name():
                print(f"already on {target_name}; nothing to do", file=sys.stderr)
                return 1
            target = tracker.task_store(target_name)
            if args.dry_run:
                rows = store.list()
                print(
                    f"would move {len(rows)} record(s) from "
                    f"{store.capabilities().name} to {target.capabilities().name}"
                )
                return 0
            result = run_migration(store, target)
            print(result.summary())
            for problem in result.skipped:
                print(f"  ! {problem}", file=sys.stderr)
            if not result.ok:
                print(
                    "\nThe source is UNCHANGED. Fix the reported records and re-run, or "
                    "migrate into a clean target — this does not delete anything.",
                    file=sys.stderr,
                )
            return 0 if result.ok else 1

        if v == "backend":
            _emit(asdict(store.capabilities()), getattr(args, 'json', False))
        elif v == "show":
            t = store.show(args.id)
            if t is None:
                print(f"no such task: {args.id}", file=sys.stderr)
                return 1
            if getattr(args, "json", False):
                _rows([t], True)
            else:
                print(_record(t))
        elif v == "list":
            _rows(
                store.list(
                    type=args.type,
                    status=args.status,
                    parent=args.parent,
                    limit=args.limit,
                ),
                getattr(args, 'json', False),
            )
        elif v == "ready":
            _rows(store.ready(parent=args.parent, limit=args.limit), getattr(args, 'json', False))
        elif v == "validate":
            val = store.validate(args.epic)
            _emit(asdict(val), True)
            return 0 if val.ok else 1
        elif v == "create":
            print(
                store.create(
                    args.title,
                    type=args.type,
                    description=args.description,
                    priority=args.priority,
                    parent=args.parent,
                )
            )
        elif v == "close":
            store.close(args.id, args.reason)
        elif v == "note":
            store.note(args.id, args.text)
        elif v == "dep":
            store.dep_add(args.dependent, args.blocker)
        elif v == "export":
            store.export(args.out) if args.out else store.export()
        elif v == "update":
            fields = {
                k: getattr(args, k)
                for k in ("status", "title", "description", "priority", "parent", "assignee")
                if getattr(args, k) is not None
            }
            if args.append_notes:
                store.note(args.id, args.append_notes)
            if fields:
                store.update(args.id, **fields)
        elif v == "supersede":
            store.supersede(args.old, args.new)
        elif v == "delete":
            store.delete(args.id)
        elif v == "label":
            store.label(args.id, args.name, remove=args.action == "remove")
        elif v == "gate":
            if args.action == "create":
                print(store.gate_create(args.target, args.reason))
            elif args.action == "list":
                _rows(store.gate_list(), getattr(args, 'json', False))
            else:
                store.gate_resolve(args.target)
        elif v == "autosync":
            store.autosync(args.state == "on")
        elif v == "prime":
            print(store.prime(), end="")
        elif v == "render":
            from . import render as rendermod

            if args.check:
                why = rendermod.check(store, args.epic, args.dest)
                if why:
                    print(f"DRIFT: {why}", file=sys.stderr)
                    return 1
                return 0
            if args.dest:
                rendermod.write(store, args.epic, args.dest)
            else:
                print(rendermod.render_epic(store, args.epic))
        return 0
    except NotSupported as exc:
        print(f"NOT SUPPORTED: {exc}", file=sys.stderr)
        return 3
    except TrackerError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
