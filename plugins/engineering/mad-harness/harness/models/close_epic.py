"""Close an epic — `campaign-loop` §5's mechanical tail, one call, gated before it writes.

MEASURED. §5 is eight shell lines the orchestrator ran as eight tool calls at the end of
every epic, each re-reading a context that averaged ~380k tokens across a 237-request
field run (55k -> 920k) — ~$0.11-0.17 per line, ~6x a worker's rate, to learn an exit
status. And the lines are not independent: three are GATES (no prose-only block, a clean
decision register, a retired staging folder) and five are WRITES (close, export, commit,
push, restore autosync) that must not start until every gate has passed. Run by hand,
the gates were read one at a time and the writes began on the strength of whichever the
orchestrator remembered; run here, the order is the code.

    exit 0   gates passed, every write done (or --check: gates passed, nothing written)
    exit 1   a gate failed — NOTHING was written, the report says which
             or a write failed — the report says which step, and what remains by hand
    exit 2   usage: `--reason` is required unless `--check`

THE THREE GATES ARE ALL-OR-NOTHING. A gate that failed after `tk.sh close` had already
run is an epic closed over an open decision, with the tracker export now disagreeing
with the register. So every gate runs first, and the first write starts only when all
three passed. `--check` runs exactly the gates and stops — the way to ask "could this
close?" without closing it.

THE TRACKED EXPORT IS ASKED FOR, NOT GUESSED. `tk.sh backend --json` declares
`export_path`; that is what `git add` names. Not `owned_paths`: for beads that prefix
also holds `config.yaml`, which `autosync off` rewrote at §0 — committing it here would
record `export.auto: false` and dirty the tree again when step (h) restores it.

`git pull --rebase --autostash`, not `--rebase` alone: the same `config.yaml` is an
unstaged change at this point in every beads-backed run, and a plain rebase refuses to
start over it. Autostash carries it across and puts it back; nothing uncommitted is lost.

0.10.20: THE STEPS BEFORE THE ONE CALL JOIN IT. §5's text still had four lines the
orchestrator ran by hand before `close-epic.sh` — "every child is closed or gated",
"regenerate the view one last time BEFORE retiring the folder", `archive-epic.sh`, and
"fold in ② first" — and the order among them was the orchestrator's to remember. They are
gates (0) and (0b) and pre-writes (0c) and (0d) here, run only when (0) and (0b) passed,
so the archived folder is the one with the final view in it. The archive's `git mv` and
its frontmatter stamps are then committed WITH the export — before this, the stamps were
edits `archive_epic` made after the `git mv`, unstaged, and every close left them dirty.
The write tail (e)-(h) is now `tracker_sync.sync`, which `/swarm`, `/grind` and `/halt`
share instead of each carrying its own prose copy.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import tracker_sync
from .project import Project, ProjectError, load
from .resolve import HARNESS, REPO
from .steps import (
    FAIL,
    INFO,
    OK,
    SKIP,
    Raw,
    Result,
    execute,
    failure_detail,
    render,
    tail,
)

CHECKS = HARNESS / "checks"
TK = HARNESS / "tracker" / "tk.sh"
RENDER = HARNESS / "tracker" / "render-epic.sh"
#: How many surviving staged files the gate names before summarising.
LEFTOVERS_SHOWN = 8


def _ok_detail(raw: Raw) -> str:
    return tail(raw.stdout, 1) or tail(raw.stderr, 1) or "ok"


# --- the gates ---------------------------------------------------------------------


def _children(epic: str, runner, cwd: str) -> Result:
    """(0) Every child is closed, or blocked (gated). An epic does not close over a
    child somebody could still pick up: `ready` would offer it under a closed parent."""
    name = f"tk.sh list --parent {epic}"
    raw = execute([str(TK), "list", "--parent", epic, "--json"], cwd=cwd, runner=runner)
    if not raw.ran or raw.returncode != 0:
        return Result(name, FAIL, failure_detail(raw), raw)
    try:
        rows = json.loads(raw.stdout.strip() or "[]")
    except ValueError:
        return Result(name, FAIL, f"list answered but not in JSON:\n{tail(raw.stdout)}", raw)
    live = [r for r in rows if isinstance(r, dict) and r.get("status") not in ("closed", "blocked")]
    if live:
        shown = "\n".join(f"  {r.get('id')}  {r.get('status')}  {r.get('title', '')}"[:160] for r in live[:LEFTOVERS_SHOWN])
        more = f"\n  … {len(live) - LEFTOVERS_SHOWN} more" if len(live) > LEFTOVERS_SHOWN else ""
        return Result(name, FAIL, f"{len(live)} child(ren) neither closed nor gated — close them, or gate them with a reason:\n{shown}{more}", raw)
    return Result(name, OK, f"{len(rows)} child(ren), every one closed or gated", raw)


def _folded_in(epic: str, project: Project, cwd: str) -> Result:
    """(0b) Fold-in ② has happened: no `design.md` survives in the staging folder. The
    design is routed by content — decision record, architecture doc, feature doc — by a
    `spec-editor` dispatch, and the file deleted; archiving a folder that still holds
    it is "silently discarded" with a stamp on it."""
    from tracker.staging import known_prefix, staged_folder

    name = "fold-in ② done"
    proposed = (project.paths or {}).get("proposed")
    if not proposed:
        return Result(name, FAIL, "harness.yaml declares no paths.proposed — cannot locate staged files")
    folder, _ = staged_folder(epic, Path(cwd) / proposed, known_prefix())
    if folder is not None and (folder / "design.md").is_file():
        return Result(
            name, FAIL,
            f"{(folder / 'design.md').relative_to(cwd)} survives — route it (decision record / architecture doc / "
            f"feature doc) via spec-editor and delete it, then run again",
        )
    return Result(name, OK, "no design.md in the staging folder" if folder is not None else "no staging folder")


def _render_then_archive(epic: str, project: Project, runner, cwd: str, *, check: bool) -> tuple[list[Result], list[str]]:
    """(0c) render the view one last time, then (0d) archive the folder — in that order,
    so the archived copy is the one with the final view in it. Only when a staging
    folder exists AND an archive is declared: with no archive, deletion is the
    retirement and gate (c) asks for it. Returns the results and the paths to commit."""
    from tracker.staging import known_prefix, staged_folder

    out: list[Result] = []
    proposed = (project.paths or {}).get("proposed")
    try:
        archive = project.archive_dir()
    except ProjectError as exc:
        return [Result("archive-epic.sh", FAIL, str(exc))], []
    folder, _ = staged_folder(epic, Path(cwd) / proposed, known_prefix()) if proposed else (None, [])
    if folder is None or not archive:
        why = "no staging folder" if folder is None else "no paths.archive declared — deletion is the retirement"
        return [Result("render-epic.sh / archive-epic.sh", OK, f"{why}; nothing to render or archive")], []
    view = (folder / "tasks.md").relative_to(cwd)
    if check:
        n = sum(1 for p in folder.rglob("*") if p.is_file())
        return [
            Result(f"render-epic.sh {epic}", SKIP, f"would write {view} (--check)"),
            Result(f"archive-epic.sh {epic}", SKIP, f"would archive {folder.relative_to(cwd)} ({n} file(s)) (--check)"),
        ], []
    raw = execute([str(RENDER), epic, "--write", str(view)], cwd=cwd, runner=runner)
    if not raw.ran or raw.returncode != 0:
        return [Result(f"render-epic.sh {epic}", FAIL, failure_detail(raw), raw)], []
    out.append(Result(f"render-epic.sh {epic}", OK, str(view), raw))
    raw = execute([str(CHECKS / "archive-epic.sh"), epic], cwd=cwd, runner=runner)
    if not raw.ran or raw.returncode != 0:
        out.append(Result(f"archive-epic.sh {epic}", FAIL, failure_detail(raw), raw))
        return out, []
    dest = (raw.stdout.strip().splitlines() or [""])[0].strip()
    out.append(Result(f"archive-epic.sh {epic}", OK, f"archived at {dest}" if dest else _ok_detail(raw), raw))
    return out, [dest] if dest else []


def _blocking_prose(runner, cwd: str) -> Result:
    """(a) No task is blocked in prose only. `--strict`, because a finding must FAIL the
    gate: without it the check is advisory and exits 0 over the very thing §5 forbids."""
    name = "check-blocking-prose.sh --strict"
    raw = execute([str(CHECKS / "check-blocking-prose.sh"), "--strict"], cwd=cwd, runner=runner)
    if raw.ran and raw.returncode == 0:
        return Result(name, OK, _ok_detail(raw), raw)
    return Result(name, FAIL, failure_detail(raw), raw)


def _register(epic: str, runner, cwd: str) -> Result:
    """(b) The decision register is consistent AND its Open table is empty. The check
    exits 0 with open rows — that is a consistent register — so the marker it prints
    beside one is what turns "clean" into "clean but cannot close"."""
    from tracker.check_register import OPEN_MARKER

    name = f"check-decision-register.sh {epic}"
    raw = execute([str(CHECKS / "check-decision-register.sh"), epic], cwd=cwd, runner=runner)
    if not raw.ran or raw.returncode != 0:
        return Result(name, FAIL, failure_detail(raw), raw)
    if OPEN_MARKER in raw.stdout:
        opened = [ln.strip() for ln in raw.stdout.splitlines() if OPEN_MARKER in ln]
        return Result(
            name, FAIL,
            "open decision(s) in the register — an epic does not close over a decision it raised:\n"
            + "\n".join(opened) + "\nResolve them (or gate the epic on them) and run again.", raw,
        )
    return Result(name, OK, _ok_detail(raw), raw)


def _archived(epic: str, archive: Path, forms: tuple[str, ...]) -> Path | None:
    """`<archive>/<date>-<form>[-<slug>]` for any spelling of the id, or None."""
    if not archive.is_dir():
        return None
    for form in forms:
        for p in sorted(archive.glob(f"*-{form}*")):
            stem = p.name.split("-", 3)[-1] if p.name[:4].isdigit() else p.name
            if p.is_dir() and (stem == form or stem.startswith(f"{form}-")):
                return p
    return None


def _staging(epic: str, project: Project, runner, cwd: str) -> Result:
    """(c) The staging folder is absent or empty — AND, where an archive is declared, the
    archive entry exists. Emptiness alone is not evidence of fold-in: a folder deleted
    without folding anything in is just as empty. So with an archive declared and no
    entry, git is asked whether anything was EVER staged; only "never" passes."""
    from tracker.staging import id_forms, known_prefix, staged_folder

    name = "staging folder retired"
    proposed = (project.paths or {}).get("proposed")
    if not proposed:
        return Result(name, FAIL, "harness.yaml declares no paths.proposed — cannot locate staged files")
    forms = id_forms(epic, known_prefix())
    folder, tried = staged_folder(epic, Path(cwd) / proposed, known_prefix())
    if folder is not None:
        left = sorted(str(p.relative_to(cwd)) for p in folder.rglob("*") if p.is_file())
        if left:
            shown = "\n".join(left[:LEFTOVERS_SHOWN])
            more = f"\n… {len(left) - LEFTOVERS_SHOWN} more" if len(left) > LEFTOVERS_SHOWN else ""
            return Result(
                name, FAIL,
                f"{len(left)} staged file(s) survive their epic — a second source of truth. "
                f"Fold in ② (design.md, proposal.md, decisions.md), then "
                f"`{CHECKS / 'archive-epic.sh'} {epic}` (or delete the folder where no archive is declared):\n{shown}{more}",
            )
    try:
        archive = project.archive_dir()
    except ProjectError as exc:
        return Result(name, FAIL, str(exc))
    if not archive:
        where = folder.relative_to(cwd) if folder else ", ".join(tried)
        return Result(name, OK, f"{where} {'is empty' if folder else 'absent'}; no paths.archive declared, so deletion is the retirement")
    entry = _archived(epic, Path(cwd) / archive, forms)
    if entry is not None:
        return Result(name, OK, f"archived at {entry.relative_to(cwd)}")
    # Nothing on disk and nothing archived. Was there ever anything to retire?
    specs = [f"{proposed.rstrip('/')}/{form}*" for form in forms]
    raw = execute(["git", "log", "-n", "1", "--diff-filter=A", "--format=%h", "--", *specs], cwd=cwd, runner=runner)
    if raw.ran and raw.returncode == 0 and not raw.stdout.strip():
        return Result(name, OK, f"nothing was ever staged for this epic (no commit added {' or '.join(specs)}); nothing to archive")
    if not raw.ran or raw.returncode != 0:
        return Result(name, FAIL, f"paths.archive is declared but no {archive}/*-{epic}-* exists, and git could not say whether anything was staged:\n{failure_detail(raw)}", raw)
    return Result(
        name, FAIL,
        f"paths.archive is declared but no {archive}/*-{epic}-* exists — yet commit {raw.stdout.strip()} staged files "
        f"for this epic. Retired without an archive entry is indistinguishable from discarded: restore the folder "
        f"from that commit, fold it in, and run `{CHECKS / 'archive-epic.sh'} {epic}`.", raw,
    )


def gate(epic: str, project: Project, *, runner=None, cwd: str | None = None) -> list[Result]:
    """(a)-(c), every one of them, in order. All run even when the first fails: the report
    is the whole reason to ask, and a gate skipped is a gate the orchestrator re-runs."""
    at = cwd or str(REPO)
    return [
        _blocking_prose(runner, at),
        _register(epic, runner, at),
        _staging(epic, project, runner, at),
    ]


# --- the writes --------------------------------------------------------------------


def by_hand(epic: str, reason: str, export_path: str | None, push: bool, extra: list[str] = ()) -> list[tuple[str, str]]:
    """(step, command) for every write, as the orchestrator would type it. The report's
    "remaining" list is a suffix of this."""
    quoted = reason.replace('"', '\\"')
    return [("close", f'{TK} close {epic} --reason "{quoted}"')] + tracker_sync.by_hand(
        f"chore(tracker): close {epic}", export_path, [], list(extra), push, True
    )


#: Re-exported: the tests and the docs name it here, and the backend question is asked
#: BEFORE `tk.sh close` so a backend that cannot answer stops the close before it starts.
export_path = tracker_sync.export_path


def close(
    epic: str,
    reason: str,
    *,
    push: bool = True,
    extra_paths: list[str] = (),
    runner=None,
    cwd: str | None = None,
) -> list[Result]:
    """(d) close, then the sync tail (e)-(h), stopping at the first failure. The failing
    result carries what remains. `extra_paths` — the archived folder — commits with the
    export, so the archive's `git mv` and its stamps land in the same commit."""
    at = cwd or str(REPO)
    results: list[Result] = []

    path, raw = export_path(runner, at)
    if raw.error or (raw.ran and raw.returncode != 0):
        plan = by_hand(epic, reason, "<the tracked export>", push, list(extra_paths))
        results.append(Result("tk.sh backend --json", FAIL, "cannot learn the tracked export path:\n" + failure_detail(raw), raw, remaining=[c for _, c in plan]))
        return results

    # (d)
    name = f"tk.sh close {epic} --reason …"
    raw = execute([str(TK), "close", epic, "--reason", reason], cwd=at, runner=runner)
    if not raw.ran or raw.returncode != 0:
        plan = by_hand(epic, reason, path, push, list(extra_paths))
        results.append(Result(name, FAIL, failure_detail(raw), raw, remaining=[c for _, c in plan]))
        return results
    results.append(Result(name, OK, "closed", raw))

    # (e)-(h) — the shared tail. An epic's commit is the tracker export alone, so an
    # upstream that moved under the rebase changes nothing about it: no stop.
    results += tracker_sync.sync(
        message=f"chore(tracker): close {epic}", epics=[], export=path, extra_paths=list(extra_paths),
        push=push, stop_if_upstream_moved=False, restore_autosync=True, runner=runner, cwd=at,
    )
    if any(r.status == FAIL for r in results):
        return results

    # The epic lease, if this machine holds one. Idempotent on the remote — releasing a
    # lease nobody holds is success — and never a reason to call the close failed: a
    # lease that outlives its epic expires on its TTL.
    if push:
        raw = execute([str(TK), "lease", "release", epic], cwd=at, runner=runner)
        ok = raw.ran and raw.returncode == 0
        results.append(Result(f"tk.sh lease release {epic}", INFO, "released" if ok else f"not released — {failure_detail(raw)} (it expires on its TTL)", raw))
    return results


# --- the command ----------------------------------------------------------------------


def report(epic: str, gates: list[Result], writes: list[Result] | None, check: bool) -> tuple[str, int]:
    """The whole report and the exit status, from the two phases' results."""
    out = [f"close-epic {epic} — gates", render(gates)]
    if any(r.status == FAIL for r in gates):
        if any(r.status == OK and r.name.startswith("archive-epic.sh") for r in gates):
            out.append("\nGATE FAILED after the folder was archived — the archive is staged and uncommitted; resolve the [FAIL] line(s) and run again, which commits it with the close.")
        else:
            out.append("\nGATE FAILED — nothing written. Resolve the [FAIL] line(s) above and run again.")
        return "\n".join(out), 1
    if check:
        out.append("\nGATES PASSED — nothing written (--check). Drop --check to close.")
        return "\n".join(out), 0
    out += ["", f"close-epic {epic} — writes", render(writes or [])]
    failed = next((r for r in (writes or []) if r.status == FAIL), None)
    if failed:
        out.append(f"\nSTOPPED at `{failed.name}` — the steps before it are done. Remaining, by hand, in this order:")
        out += [f"  {c}" for c in failed.remaining]
        return "\n".join(out), 1
    out.append(f"\nCLOSED {epic}. Every §5 step ran; the tracker's export is committed and autosync is back on.")
    return "\n".join(out), 0


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(
        prog="close-epic.sh",
        description=(
            "campaign-loop §5 in one call: gate (every child closed or gated, fold-in ② done), "
            "render the view and archive the folder, gate (no prose-only blocks, register clean and "
            "nothing open, staging folder retired), then close, export, commit, pull, push, "
            "autosync on, lease release. A failed gate writes nothing."
        ),
    )
    ap.add_argument("epic", help="the epic to close, either id form")
    ap.add_argument("--reason", default=None, help='what shipped and how it was verified — required unless --check')
    ap.add_argument("--check", action="store_true", help="run the three gates only and report; never writes")
    ap.add_argument("--no-push", dest="push", action="store_false", help="stop after the commit; the push is yours")
    args = ap.parse_args(argv)
    if not args.check and not args.reason:
        ap.error("--reason is required: `tk.sh close` refuses a close without one")

    try:
        project = load()
    except ProjectError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    text, code = run(args.epic, args.reason or "", project, check=args.check, push=args.push)
    print(text)
    return code


def run(
    epic: str,
    reason: str,
    project: Project,
    *,
    check: bool = False,
    push: bool = True,
    runner=None,
    cwd: str | None = None,
) -> tuple[str, int]:
    """The whole command: (0) children, (0b) fold-in ②, then — only when both passed —
    (0c) render and (0d) archive, then gates (a)-(c), then — only when every gate passed
    and this is not `--check` — the writes. Returns the report and the exit status.

    (0c)/(0d) are writes that must PRECEDE gate (c): the staging gate asks for a retired
    folder, and the archived folder should hold the final view. So they run between two
    gate phases, on the strength of (0) and (0b) alone, and what they staged commits
    with the close. A failure in them stops before (a)."""
    at = cwd or str(REPO)
    pre = [_children(epic, runner, at), _folded_in(epic, project, at)]
    extra: list[str] = []
    if not any(r.status == FAIL for r in pre):
        staged, extra = _render_then_archive(epic, project, runner, at, check=check)
        pre += staged
    if any(r.status == FAIL for r in pre):
        return report(epic, pre, None, check)
    gates = pre + gate(epic, project, runner=runner, cwd=cwd)
    if check or any(r.status == FAIL for r in gates):
        return report(epic, gates, None, check)
    writes = close(epic, reason, push=push, extra_paths=extra, runner=runner, cwd=cwd)
    return report(epic, gates, writes, False)


if __name__ == "__main__":
    raise SystemExit(main())
