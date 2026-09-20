"""Close a wave's tasks and sync the tracker — `/swarm` step 9 and `/grind` §10 as one call.

WHAT THIS REPLACES. `/swarm` step 9 was ten shell lines the orchestrator ran one per tool
call at the end of every wave; `/grind` §10 was the same ten, once per TASK — the
highest-frequency bookkeeping in the plugin — and `/halt` §4 a third copy. Each said
"in this order, every time" and spent a paragraph on why `tk.sh export` is load-bearing,
because each was a place the orchestrator could skip a line. Two of the three also said
`git pull --rebase`, the form that refuses to start on a beads repo after `autosync off`.
The measured cost is the same as for every collapsed sequence (`steps.py`): ~$0.11-0.17
per line at the orchestrator's context, ~6x a worker's.

THE CLOSES, THEN ONE SYNC. Each task closes with its own reason, in ascending id order,
stopping at the first failure with the rest listed by hand; then `tracker_sync.sync` runs
once — export, the epic view for every epic the closed tasks belong to, add, commit, pull,
push, status — and `autosync on` only when this run is the whole run.

AUTOSYNC IS THE ONE FLAG THAT DEPENDS ON WHO IS CALLING. `/swarm` step 9 restored
`export.auto` at the end of every wave; `campaign-loop` §0 says only §5 restores it. Both
were right for their caller and wrong for the other: a standalone `/swarm` IS the run and
must restore what its pre-flight disabled, while a wave inside a campaign re-enabling it
would have the backend staging `issues.jsonl` into the next wave's worker commits — the
exact thing §0 turns it off to prevent. The two documents disagreed on a state transition
for months. Here it is `--restore-autosync` (default off): `/swarm` and `/grind` pass it;
`campaign-loop` §4 does not, and `close-epic.sh` restores it at §5.

UPSTREAM MOVED STOPS BEFORE THE PUSH. A wave's commit sits on code the wave gate proved
green; a rebase that pulls in another actor's commits puts it on code nobody gated. The
sync counts what the pull brought in and stops with the push listed as remaining; the
orchestrator re-runs the gate, then pushes. `/grind`'s per-task close passes the same
flag: its rule is "re-run the test suite before pushing" for the same reason.
"""

from __future__ import annotations

import json
import sys

from . import tracker_sync
from .project import ProjectError, load
from .resolve import HARNESS, REPO
from .steps import FAIL, INFO, OK, Result, execute, failure_detail, render, tail

TK = HARNESS / "tracker" / "tk.sh"


def parse_closes(items: list[str], default_reason: str | None) -> tuple[list[tuple[str, str]], str]:
    """`<id>` or `<id>=<reason>`, each with a reason from the item or `--reason`.
    Returns the (id, reason) pairs sorted by id, or the usage error."""
    out: list[tuple[str, str]] = []
    for item in items:
        tid, sep, reason = item.partition("=")
        tid = tid.strip()
        if not tid:
            return [], f"empty task id in {item!r}"
        reason = reason.strip() if sep else (default_reason or "")
        if not reason:
            return [], f"{tid}: no reason — write `{tid}=<what shipped, how verified>` or give --reason for all"
        out.append((tid, reason))
    out.sort(key=lambda p: p[0])
    return out, ""


def epics_of(ids: list[str], runner, cwd: str) -> tuple[list[str], list[Result]]:
    """The distinct parents of the tasks, in first-seen order, from `tk.sh show --json`.
    A task the tracker cannot show is reported and skipped: the view for an epic that
    cannot be found is not the reason to fail a close that already happened."""
    seen: list[str] = []
    notes: list[Result] = []
    for tid in ids:
        raw = execute([str(TK), "show", tid, "--json"], cwd=cwd, runner=runner)
        if not raw.ran or raw.returncode != 0:
            notes.append(Result(f"tk.sh show {tid}", FAIL, failure_detail(raw), raw))
            continue
        try:
            rows = json.loads(raw.stdout.strip() or "[]")
            row = rows[0] if isinstance(rows, list) else rows
            parent = row.get("parent")
        except (ValueError, IndexError, AttributeError):
            notes.append(Result(f"tk.sh show {tid}", FAIL, f"show answered but not in JSON:\n{tail(raw.stdout)}", raw))
            continue
        if parent and parent not in seen:
            seen.append(parent)
    return seen, notes


def check(closes: list[tuple[str, str]], runner, cwd: str) -> list[Result]:
    """`--check`: every id exists and is open. Writes nothing."""
    out: list[Result] = []
    for tid, reason in closes:
        raw = execute([str(TK), "show", tid, "--json"], cwd=cwd, runner=runner)
        if not raw.ran or raw.returncode != 0:
            out.append(Result(f"tk.sh show {tid}", FAIL, failure_detail(raw), raw))
            continue
        try:
            rows = json.loads(raw.stdout.strip() or "[]")
            row = rows[0] if isinstance(rows, list) else rows
            status = row.get("status")
        except (ValueError, IndexError, AttributeError):
            out.append(Result(f"tk.sh show {tid}", FAIL, f"show answered but not in JSON:\n{tail(raw.stdout)}", raw))
            continue
        if status == "closed":
            out.append(Result(f"tk.sh show {tid}", FAIL, "already closed — closing it again fails against a compacted record", raw))
        else:
            out.append(Result(f"tk.sh show {tid}", OK, f"{status} — would close: {reason[:80]}", raw))
    return out


def close_all(closes: list[tuple[str, str]], plan_tail: list[str], runner, cwd: str) -> list[Result]:
    """Every close, ascending by id, stopping at the first failure with the rest by
    hand. The order is enforced HERE, not trusted from the caller: it is what makes a
    stopped run's "remaining" list unambiguous."""
    out: list[Result] = []
    closes = sorted(closes, key=lambda p: p[0])
    for i, (tid, reason) in enumerate(closes):
        name = f"tk.sh close {tid} --reason …"
        raw = execute([str(TK), "close", tid, "--reason", reason], cwd=cwd, runner=runner)
        if not raw.ran or raw.returncode != 0:
            rest = [f'{TK} close {t} --reason "{r}"' for t, r in closes[i:]] + plan_tail
            out.append(Result(name, FAIL, failure_detail(raw), raw, remaining=rest))
            return out
        out.append(Result(name, OK, "closed", raw))
    return out


def run(
    closes: list[tuple[str, str]],
    *,
    message: str | None = None,
    epics: list[str] | None = None,
    push: bool = True,
    restore_autosync: bool = False,
    sync_only: bool = False,
    check_only: bool = False,
    project=None,
    runner=None,
    cwd: str | None = None,
    wave: str | None = None,
) -> tuple[str, int]:
    """The whole command. Returns the report and the exit status."""
    at = cwd or str(REPO)
    ids = [t for t, _ in closes]
    head = "close-wave" + (f" {' '.join(ids)}" if ids else " --sync-only")

    if check_only:
        results = check(closes, runner, at) if closes else [Result("--check", OK, "nothing to close (--sync-only)")]
        out = [f"{head} — check", render(results)]
        failed = any(r.status == FAIL for r in results)
        out.append("\nCHECK FAILED — nothing written." if failed else "\nCHECK PASSED — nothing written (--check). Drop --check to close.")
        return "\n".join(out), 1 if failed else 0

    results: list[Result] = []
    # The backend question first: a backend that cannot answer stops the wave before any
    # close, as it does an epic close.
    export, raw = tracker_sync.export_path(runner, at)
    if raw.error or (raw.ran and raw.returncode != 0):
        results.append(Result("tk.sh backend --json", FAIL, "cannot learn the tracked export path:\n" + failure_detail(raw), raw))
        return _report(head, results), 1

    which = list(epics) if epics is not None else []
    if not which and ids:
        which, notes = epics_of(ids, runner, at)
        results += notes
    msg = message or (f"chore(tracker): close {' '.join(ids)}" if ids else "chore(tracker): sync")
    tail_plan = [c for _, c in tracker_sync.by_hand(msg, export, [], [], push, restore_autosync)]

    if not sync_only:
        results += close_all(closes, tail_plan, runner, at)
        if any(r.status == FAIL and r.name.startswith("tk.sh close") for r in results):
            return _report(head, results), 1

    results += tracker_sync.sync(
        message=msg, epics=which, export=export, push=push, stop_if_upstream_moved=True,
        restore_autosync=restore_autosync, project=project, runner=runner, cwd=at,
    )
    failed = any(r.status == FAIL for r in results)
    if wave:
        try:
            from . import wave_manifest

            path = wave_manifest.path_for(wave)
            for tid in ids:
                wave_manifest.append(path, "closed", tid)
            if not failed:
                head_raw = execute(["git", "rev-parse", "HEAD"], cwd=at, runner=runner)
                wave_manifest.close(path, head_raw.stdout.strip() if head_raw.ran else "")
                wave_manifest.heartbeat(path, "PUSHED", f"closed={len(ids)}", runner=runner)
            results.append(Result("manifest", INFO, f"{path.name}: {len(ids)} closed" + ("" if failed else "; wave closed")))
        except (OSError, ValueError) as exc:
            results.append(Result("manifest", INFO, f"not recorded — {exc}"))
    return _report(head, results), (1 if failed else 0)


def _report(head: str, results: list[Result]) -> str:
    out = [head, render(results)]
    failed = next((r for r in results if r.status == FAIL), None)
    if failed and failed.remaining:
        out.append(f"\nSTOPPED at `{failed.name}` — the steps before it are done. Remaining, by hand, in this order:")
        out += [f"  {c}" for c in failed.remaining]
    elif failed:
        out.append(f"\nSTOPPED at `{failed.name}`.")
    else:
        out.append("\nSYNCED. The tracker's export is committed" + ("" if "git push" not in " ".join(r.name for r in results) else " and pushed") + ".")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="close-wave.sh",
        description=(
            "/swarm step 9 and /grind §10 in one call: close each task with its reason, then "
            "export, regenerate every affected epic's view, commit, pull --rebase --autostash, push, "
            "and — when this run owns the tracker — autosync on. An upstream that moved under the "
            "rebase stops before the push."
        ),
    )
    ap.add_argument("tasks", nargs="*", metavar="ID[=REASON]", help="tasks to close; a reason per task, or --reason for all")
    ap.add_argument("--reason", default=None, help="the reason for every task that does not carry its own")
    ap.add_argument("--message", default=None, help="the commit subject (default: chore(tracker): close <ids>)")
    ap.add_argument("--epic", action="append", default=None, help="regenerate this epic's view (default: the parents of the closed tasks)")
    ap.add_argument("--no-push", dest="push", action="store_false", help="stop after the commit; the push is yours")
    ap.add_argument("--restore-autosync", action="store_true", help="`tk.sh autosync on` at the end — for a run that is the whole run (/swarm, /grind), never for a wave inside a campaign")
    ap.add_argument("--sync-only", action="store_true", help="no closes; export, render, commit, push")
    ap.add_argument("--check", action="store_true", help="verify every id exists and is open; write nothing")
    ap.add_argument("--wave", default=None, help="record `closed` on this wave manifest and close it (<epic>-w<n>)")
    args = ap.parse_args(argv)

    if not args.tasks and not args.sync_only:
        ap.error("name the tasks to close, or --sync-only")
    closes, why = parse_closes(args.tasks, args.reason)
    if why:
        ap.error(why)
    try:
        project = load()
    except ProjectError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    text, code = run(
        closes, message=args.message, epics=args.epic, push=args.push,
        restore_autosync=args.restore_autosync, sync_only=args.sync_only, check_only=args.check, project=project, wave=args.wave,
    )
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
