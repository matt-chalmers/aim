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
"""

from __future__ import annotations

import json
from pathlib import Path

from .project import Project, ProjectError, load
from .resolve import HARNESS, REPO
from .steps import (
    FAIL,
    OK,
    REMOTE_TIMEOUT,
    Raw,
    Result,
    execute,
    failure_detail,
    render,
    tail,
)

CHECKS = HARNESS / "checks"
TK = HARNESS / "tracker" / "tk.sh"
#: How many surviving staged files the gate names before summarising.
LEFTOVERS_SHOWN = 8


def _ok_detail(raw: Raw) -> str:
    return tail(raw.stdout, 1) or tail(raw.stderr, 1) or "ok"


# --- the gates ---------------------------------------------------------------------


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


def by_hand(epic: str, reason: str, export_path: str | None, push: bool) -> list[tuple[str, str]]:
    """(step, command) for every write, as the orchestrator would type it. The report's
    "remaining" list is a suffix of this."""
    quoted = reason.replace('"', '\\"')
    steps = [
        ("close", f'{TK} close {epic} --reason "{quoted}"'),
        ("export", f"{TK} export"),
        ("commit", f'git add {export_path} && git commit -m "chore(tracker): close {epic}"' if export_path else "(no tracked export — nothing to commit)"),
    ]
    if push:
        steps += [("pull", "git pull --rebase --autostash"), ("push", "git push")]
    steps.append(("autosync", f"{TK} autosync on"))
    return steps


def export_path(runner, cwd: str) -> tuple[str | None, Raw]:
    """What the backend declares as its tracked artefact. None is an answer: the backend
    keeps no tracked export, and the commit step says so rather than adding nothing."""
    raw = execute([str(TK), "backend", "--json"], cwd=cwd, runner=runner)
    if not raw.ran or raw.returncode != 0:
        return None, raw
    try:
        caps = json.loads(raw.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None, Raw(None, raw.stdout, raw.stderr, error=f"backend --json did not answer in JSON:\n{tail(raw.stdout)}")
    path = caps.get("export_path")
    return (str(path) if path else None), raw


def close(
    epic: str,
    reason: str,
    *,
    push: bool = True,
    runner=None,
    cwd: str | None = None,
) -> list[Result]:
    """(d)-(h), stopping at the first failure. The failing result carries what remains."""
    at = cwd or str(REPO)
    results: list[Result] = []

    path, raw = export_path(runner, at)
    if raw.error or (raw.ran and raw.returncode != 0):
        plan = by_hand(epic, reason, "<the tracked export>", push)
        results.append(Result("tk.sh backend --json", FAIL, "cannot learn the tracked export path:\n" + failure_detail(raw), raw, remaining=[c for _, c in plan]))
        return results
    plan = by_hand(epic, reason, path, push)
    names = [s for s, _ in plan]

    def fail(step: str, name: str, detail: str, raw: Raw | None = None) -> list[Result]:
        results.append(Result(name, FAIL, detail, raw, remaining=[c for s, c in plan if names.index(s) >= names.index(step)]))
        return results

    # (d)
    name = f"tk.sh close {epic} --reason …"
    raw = execute([str(TK), "close", epic, "--reason", reason], cwd=at, runner=runner)
    if not raw.ran or raw.returncode != 0:
        return fail("close", name, failure_detail(raw), raw)
    results.append(Result(name, OK, "closed", raw))

    # (e)
    raw = execute([str(TK), "export"], cwd=at, runner=runner)
    if not raw.ran or raw.returncode != 0:
        return fail("export", "tk.sh export", failure_detail(raw), raw)
    results.append(Result("tk.sh export", OK, "tracked export regenerated", raw))

    # (f)
    if path is None:
        results.append(Result("git commit", OK, "the backend declares no tracked export — nothing to commit"))
    else:
        name = f"git add {path} && git commit"
        raw = execute(["git", "add", "--", path], cwd=at, runner=runner)
        if not raw.ran or raw.returncode != 0:
            return fail("commit", name, failure_detail(raw), raw)
        staged = execute(["git", "diff", "--cached", "--quiet"], cwd=at, runner=runner)
        if not staged.ran or staged.returncode not in (0, 1):
            return fail("commit", name, failure_detail(staged), staged)
        if staged.returncode == 0:
            results.append(Result(name, OK, "nothing staged — the export already matched HEAD; no commit made", raw))
        else:
            raw = execute(["git", "commit", "-m", f"chore(tracker): close {epic}"], cwd=at, runner=runner)
            if not raw.ran or raw.returncode != 0:
                return fail("commit", name, failure_detail(raw), raw)
            results.append(Result(name, OK, f"chore(tracker): close {epic} — {_ok_detail(raw)}", raw))

    # (g)
    if push:
        raw = execute(["git", "pull", "--rebase", "--autostash"], cwd=at, timeout=REMOTE_TIMEOUT, runner=runner)
        if not raw.ran or raw.returncode != 0:
            return fail("pull", "git pull --rebase --autostash", failure_detail(raw), raw)
        results.append(Result("git pull --rebase --autostash", OK, _ok_detail(raw), raw))
        raw = execute(["git", "push"], cwd=at, timeout=REMOTE_TIMEOUT, runner=runner)
        if not raw.ran or raw.returncode != 0:
            return fail("push", "git push", failure_detail(raw), raw)
        results.append(Result("git push", OK, _ok_detail(raw), raw))
    else:
        results.append(Result("git pull / git push", OK, "skipped (--no-push) — push by hand: git pull --rebase --autostash && git push"))

    # (h)
    raw = execute([str(TK), "autosync", "on"], cwd=at, runner=runner)
    if not raw.ran or raw.returncode != 0:
        return fail("autosync", "tk.sh autosync on", failure_detail(raw), raw)
    results.append(Result("tk.sh autosync on", OK, "restored — what §0 disabled", raw))
    return results


# --- the command ----------------------------------------------------------------------


def report(epic: str, gates: list[Result], writes: list[Result] | None, check: bool) -> tuple[str, int]:
    """The whole report and the exit status, from the two phases' results."""
    out = [f"close-epic {epic} — gates", render(gates)]
    if any(r.status == FAIL for r in gates):
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
            "campaign-loop §5 in one call: gate (no prose-only blocks, register clean and "
            "nothing open, staging folder retired), then close, export, commit, pull, push, "
            "autosync on. A failed gate writes nothing."
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
    """The whole command: gates, then — only when every gate passed and this is not
    `--check` — the writes. Returns the report and the exit status."""
    gates = gate(epic, project, runner=runner, cwd=cwd)
    if check or any(r.status == FAIL for r in gates):
        return report(epic, gates, None, check)
    writes = close(epic, reason, push=push, runner=runner, cwd=cwd)
    return report(epic, gates, writes, False)


if __name__ == "__main__":
    raise SystemExit(main())
