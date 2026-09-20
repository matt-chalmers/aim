"""Stop a swarm or a campaign cleanly — `/halt` as one call per form.

WHAT THIS REPLACES. `/halt` was twenty to twenty-five tool calls at the orchestrator's
context price, run when something had already gone wrong and an operator was under
pressure: §1 assess (seven reads, one a shell `while` loop over the worktrees), §2 pause
(the park, then `resume-point.sh` per claim), §3 release ("First preserve. Then look. Then
release. Then remove. In that order, because each step is what makes the next one safe"),
§4 always (the slot, autosync, prune, export, commit, push). The recorded incident is an
operator following `tk.sh list --status in_progress` literally and cleaning up four
foreign tasks while leaving the two real ones claimed; `tk.sh claims` was written for it
and the prose then told the operator to run it. Here the order is the code.

THREE FORMS. `assess` reads and prints: the main tree, every worktree's uncommitted and
unmerged state, what landed, the claims (with liveness), the slot, the autosync state, and
the open waves. `pause <epic>` keeps the claims (a claimed task is already out of `ready`)
and parks the epic — `tk.sh park`, the gate AND the status — then reports where each
claimed task's work is. `release [<task>…]` hands the tasks back: preserve EVERY
worktree's work to files first (a failure here stops everything — "never remove before
preserve"), then per task the resume point, `tk.sh release --force`, a note naming where
the work was preserved and what state it was in, and — only under `--drop-uncommitted` —
the worktree removed so the next dispatch is FRESH rather than silently re-attached to a
killed run's unverified edits. Both writes end with `always`: the slot released only when
its holder is provably gone (an alive holder is a FAIL line, never forced), `git worktree
prune`, and the shared sync tail with `autosync on` — what pre-flight disabled and nothing
else restores.

THE JUDGEMENT THAT STAYS. Whether uncommitted REATTACH work is worth keeping: the default
keeps it (the branch and worktree stay; the next run re-attaches), `--drop-uncommitted`
is the operator's explicit call after reading `assess`.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from . import tracker_sync
from .resolve import HARNESS, REPO
from .steps import FAIL, INFO, OK, Raw, Result, execute, failure_detail, render, tail

TK = HARNESS / "tracker" / "tk.sh"
SWARM = HARNESS / "swarm"
EXIT_OK, EXIT_WRITE_FAILED, EXIT_USAGE = 0, 1, 2


def _json(raw: Raw):
    try:
        return json.loads((raw.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


# --- assess ----------------------------------------------------------------------------


def assess(runner, cwd: str) -> tuple[list[Result], dict]:
    """Every read, one block. Nothing here changes state."""
    from .resume import _worktrees, is_dirty, main_branch

    out: list[Result] = []
    facts: dict = {"claims": [], "slot": None, "autosync": None, "worktrees": []}

    raw = execute(["git", "status", "--porcelain"], cwd=cwd, runner=runner)
    dirty = [ln for ln in (raw.stdout if raw.ran else "").splitlines() if ln.strip()]
    out.append(Result("main tree", OK if not dirty else INFO, "clean" if not dirty else f"{len(dirty)} path(s) dirty — usually yours, not a worker's; worker edits live in the worktrees below:\n" + "\n".join(dirty[:8])))

    try:
        main = main_branch(Path(cwd))
        wts = _worktrees(Path(cwd))
    except Exception:  # noqa: BLE001
        main, wts = "main", {}
    for branch, path in sorted(wts.items()):
        if not path.is_dir():
            continue
        ahead_raw = execute(["git", "rev-list", "--count", f"{main}..HEAD"], cwd=str(path), runner=runner)
        ahead = int(ahead_raw.stdout.strip() or 0) if ahead_raw.ran and ahead_raw.returncode == 0 else -1
        try:
            unc = is_dirty(path)
        except Exception:  # noqa: BLE001
            unc = None
        facts["worktrees"].append({"branch": branch, "path": str(path), "ahead": ahead, "uncommitted": unc})
        state = ("UNCOMMITTED work" if unc else "clean") + f", {ahead} commit(s) ahead of {main}" if ahead >= 0 else "state unknown"
        out.append(Result(f"worktree {branch}", INFO, f"{path}  — {state}"))
    if not facts["worktrees"]:
        out.append(Result("worktrees", OK, "none — nothing a killed writer could have stranded"))

    raw = execute(["git", "log", "--oneline", "-10"], cwd=cwd, runner=runner)
    out.append(Result("git log --oneline -10", INFO, tail(raw.stdout, 4) if raw.ran else failure_detail(raw)))

    raw = execute([str(TK), "claims", "--json"], cwd=cwd, runner=runner)
    rows = _json(raw) if raw.ran and raw.returncode == 0 else None
    if isinstance(rows, list):
        facts["claims"] = rows
        if rows:
            shown = "\n".join(f"  {c.get('task')}  held by {c.get('holder')} on {c.get('host')}, {int(c.get('age_s') or 0) // 60}m, {'alive' if c.get('alive') else ('STALE' if c.get('stale') else 'not provably alive')}" for c in rows)
            out.append(Result("tk.sh claims", INFO, f"{len(rows)} claim(s) — the authority on what is held, not `--status in_progress`:\n{shown}", raw))
        else:
            out.append(Result("tk.sh claims", OK, "none held", raw))
    else:
        out.append(Result("tk.sh claims", FAIL, failure_detail(raw), raw))

    raw = execute([str(TK), "slot-check", "--json"], cwd=cwd, runner=runner)
    st = _json(raw) if raw.ran and raw.returncode == 0 else None
    if isinstance(st, dict):
        facts["slot"] = st
        if st.get("free"):
            out.append(Result("tk.sh slot-check", OK, "free", raw))
        else:
            out.append(Result("tk.sh slot-check", INFO, f"held by {st.get('holder') or '?'} — {'STALE (holder gone)' if st.get('stale') else 'alive'}", raw))
    else:
        out.append(Result("tk.sh slot-check", FAIL, failure_detail(raw), raw))

    raw = execute([str(TK), "backend", "--json"], cwd=cwd, runner=runner)
    caps = _json(raw) if raw.ran and raw.returncode == 0 else None
    facts["backend"] = caps if isinstance(caps, dict) else None
    out.append(Result("tk.sh backend", INFO, (f"{caps.get('name')} — export at {caps.get('export_path') or 'none'}" if isinstance(caps, dict) else failure_detail(raw)), raw))

    try:
        from . import wave_manifest as wm

        d = wm.waves_dir()
        open_waves = [wm.summary_line(wm.load(p)) for p in sorted(d.glob("*-w*.json")) if not wm.load(p).get("closed_at")] if d.is_dir() else []
    except Exception:  # noqa: BLE001
        open_waves = []
    out.append(Result("open waves", INFO if open_waves else OK, "\n".join(open_waves) if open_waves else "none"))
    return out, facts


# --- the writes --------------------------------------------------------------------------


def _resume_lines(tasks: list[str], runner, cwd: str) -> list[Result]:
    out = []
    for t in tasks:
        raw = execute([str(SWARM / "resume-point.sh"), t], cwd=cwd, runner=runner)
        out.append(Result(f"resume-point.sh {t}", INFO if raw.ran and raw.returncode == 0 else FAIL, (raw.stdout.strip() if raw.ran and raw.returncode == 0 else failure_detail(raw)), raw))
    return out


def always(epic: str | None, mode: str, facts: dict, *, push: bool, dry_run: bool, project, runner, cwd: str) -> list[Result]:
    """The slot (released only when its holder is gone), prune, then the sync tail with
    autosync restored — what pre-flight disabled and nothing else restores."""
    out: list[Result] = []
    slot = facts.get("slot") or {}
    if slot and not slot.get("free"):
        if slot.get("stale"):
            if dry_run:
                out.append(Result("tk.sh slot-release --force", INFO, f"would release (held by {slot.get('holder')}, STALE) (--dry-run)"))
            else:
                raw = execute([str(TK), "slot-release", "--force"], cwd=cwd, runner=runner)
                out.append(Result("tk.sh slot-release --force", OK if raw.ran and raw.returncode == 0 else FAIL, f"released — was held by {slot.get('holder')}, STALE" if raw.ran and raw.returncode == 0 else failure_detail(raw), raw))
        else:
            out.append(Result("tk.sh slot-release", FAIL, f"held by {slot.get('holder')} and ALIVE — not released. A slot held by a dead worker blocks the next wave forever, but this holder is not provably dead: stop it (`/tasks`), then run again"))
    else:
        out.append(Result("merge slot", OK, "free"))
    if dry_run:
        out.append(Result("git worktree prune", INFO, "would prune (--dry-run)"))
        out.append(Result("sync", INFO, "would export, commit, push and restore autosync (--dry-run)"))
        return out
    raw = execute(["git", "worktree", "prune"], cwd=cwd, runner=runner)
    out.append(Result("git worktree prune", OK if raw.ran and raw.returncode == 0 else FAIL, "pruned" if raw.ran and raw.returncode == 0 else failure_detail(raw), raw))
    export, raw = tracker_sync.export_path(runner, cwd)
    if raw.error or (raw.ran and raw.returncode != 0):
        out.append(Result("tk.sh backend --json", FAIL, "cannot learn the tracked export path:\n" + failure_detail(raw), raw))
        return out
    out += tracker_sync.sync(
        message=f"chore(tracker): halt {epic or ''} — {mode}".replace("  ", " "), epics=[epic] if epic else [], export=export,
        push=push, stop_if_upstream_moved=False, restore_autosync=True, project=project, runner=runner, cwd=cwd,
    )
    return out


def pause(epic: str, *, push: bool, dry_run: bool, project, runner, cwd: str) -> tuple[list[Result], int]:
    results, facts = assess(runner, cwd)
    results.append(Result("— pause —", INFO, "the claims are the pause: a claimed task is out of `ready`; only new work needs stopping"))
    if dry_run:
        results.append(Result(f"tk.sh park {epic}", INFO, "would park (--dry-run)"))
    else:
        raw = execute([str(TK), "park", epic, "--reason", f"paused {date.today().isoformat()}"], cwd=cwd, runner=runner)
        if not raw.ran or raw.returncode != 0:
            results.append(Result(f"tk.sh park {epic}", FAIL, failure_detail(raw) + f"\nby hand: {TK} park {epic} --reason \"paused {date.today().isoformat()}\"", raw))
            return results, EXIT_WRITE_FAILED
        results.append(Result(f"tk.sh park {epic}", OK, tail(raw.stdout, 1) or "parked — the gate AND the status", raw))
    claimed = [c.get("task") for c in facts.get("claims") or [] if c.get("task")]
    results += _resume_lines(claimed, runner, cwd)
    results += always(epic, "pause", facts, push=push, dry_run=dry_run, project=project, runner=runner, cwd=cwd)
    code = EXIT_WRITE_FAILED if any(r.status == FAIL for r in results) else EXIT_OK
    return results, code


def release(tasks: list[str], *, epic: str | None, drop_uncommitted: bool, push: bool, dry_run: bool, project, runner, cwd: str) -> tuple[list[Result], int]:
    results, facts = assess(runner, cwd)
    claimed = tasks or [c.get("task") for c in facts.get("claims") or [] if c.get("task")]
    results.append(Result("— release —", INFO, f"{len(claimed)} task(s): {', '.join(claimed) or 'none claimed'}. First preserve, then look, then release, then remove — in that order"))
    if not claimed:
        results += always(epic, "release", facts, push=push, dry_run=dry_run, project=project, runner=runner, cwd=cwd)
        return results, (EXIT_WRITE_FAILED if any(r.status == FAIL for r in results) else EXIT_OK)

    # 1. preserve — a failure here stops EVERYTHING that follows.
    if dry_run:
        results.append(Result("preserve-worktrees.sh", INFO, "would preserve every worktree's work under .harness/halted-<date>/ (--dry-run)"))
        preserved: dict[str, str] = {}
    else:
        raw = execute([str(SWARM / "preserve-worktrees.sh")], cwd=cwd, runner=runner)
        if not raw.ran or raw.returncode != 0:
            results.append(Result("preserve-worktrees.sh", FAIL, "STOPPED — nothing released and nothing removed, because nothing was preserved:\n" + failure_detail(raw), raw))
            return results, EXIT_USAGE
        preserved = {}
        for ln in raw.stdout.splitlines():
            if "preserved " in ln and "→" in ln:
                branch, _, dest = ln.split("preserved ", 1)[1].partition("→")
                preserved[branch.strip()] = dest.split("(")[0].strip()
        results.append(Result("preserve-worktrees.sh", OK, tail(raw.stdout, 2), raw))

    # 2-4. per task: look, release, note, (remove)
    today = date.today().isoformat()
    for t in claimed:
        rp = execute([str(SWARM / "resume-point.sh"), t, "--json"], cwd=cwd, runner=runner)
        info = _json(rp) if rp.ran and rp.returncode == 0 else {}
        info = info if isinstance(info, dict) else {}
        state, branch, wt = info.get("state", "?"), info.get("branch"), info.get("worktree")
        where = preserved.get(branch or "", "") or ("(nothing to preserve)" if state in ("FRESH", "MERGE", "VERIFY") else "(see preserve-worktrees.sh)")
        results.append(Result(f"resume-point.sh {t}", INFO, f"{state}" + (f" on {branch}" if branch else "") + (f", preserved at {where}" if preserved.get(branch or "") else "")))
        if dry_run:
            results.append(Result(f"tk.sh release {t} --force", INFO, "would release and note (--dry-run)"))
            continue
        raw = execute([str(TK), "release", t, "--force"], cwd=cwd, runner=runner)
        if not raw.ran or raw.returncode != 0:
            results.append(Result(f"tk.sh release {t} --force", FAIL, failure_detail(raw), raw))
            continue
        results.append(Result(f"tk.sh release {t} --force", OK, tail(raw.stdout, 1) or "released", raw))
        note = f"released {today}: {state}" + (f" on {branch}" if branch else "") + (f"; work preserved at {where}" if preserved.get(branch or "") else "") + (f"; worktree {'removed' if (state == 'REATTACH' and drop_uncommitted) else 'kept'}" if wt else "")
        raw = execute([str(TK), "update", t, "--append-notes", note], cwd=cwd, runner=runner)
        results.append(Result(f"tk.sh update {t} --append-notes", OK if raw.ran and raw.returncode == 0 else FAIL, note if raw.ran and raw.returncode == 0 else failure_detail(raw), raw))
        if state == "REATTACH" and wt:
            if drop_uncommitted:
                if not preserved.get(branch or ""):
                    results.append(Result(f"git worktree remove {wt}", FAIL, "NOT removed — preserve-worktrees.sh did not report this branch preserved; removing would lose the only copy"))
                    continue
                raw = execute(["git", "worktree", "remove", "--force", wt], cwd=cwd, runner=runner)
                results.append(Result(f"git worktree remove --force {wt}", OK if raw.ran and raw.returncode == 0 else FAIL, "removed — the next dispatch is FRESH, not re-attached to a killed run's unverified edits" if raw.ran and raw.returncode == 0 else failure_detail(raw), raw))
            else:
                results.append(Result(f"worktree {wt}", INFO, "KEPT with its uncommitted work (default). `--drop-uncommitted` removes it; the next run otherwise re-attaches to these edits"))

    results += always(epic, "release", facts, push=push, dry_run=dry_run, project=project, runner=runner, cwd=cwd)
    return results, (EXIT_WRITE_FAILED if any(r.status == FAIL for r in results) else EXIT_OK)


# --- the command ----------------------------------------------------------------------


def report(form: str, results: list[Result], code: int) -> str:
    out = [f"halt {form}", render(results)]
    if form == "assess":
        out.append("\nNothing changed. `halt.sh pause <epic>` keeps the claims and parks; `halt.sh release [<task>…]` hands them back (preserve → look → release → remove).")
    elif code == EXIT_OK:
        out.append(f"\nHALTED ({form}). The one command that resumes: `tk.sh unpark <epic>` then `/swarm` or `/campaign` — the resumed run adopts the work (resume-point.sh) rather than redoing it.")
    else:
        out.append("\nSTOPPED — read the [FAIL] line(s); each says what remains by hand.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .project import ProjectError, load

    ap = argparse.ArgumentParser(prog="halt.sh", description="/halt as one call per form: assess (reads only), pause <epic> (keep the claims, park the epic), release [<task>…] (preserve, look, release, note, optionally remove). Both writes end with the slot, prune, export, commit, push, autosync on.")
    sub = ap.add_subparsers(dest="form", required=True)
    sub.add_parser("assess", help="read and print everything a halt looks at; change nothing")
    p = sub.add_parser("pause", help="keep the claims, stop new work: park the epic")
    p.add_argument("epic")
    p.add_argument("--no-push", dest="push", action="store_false")
    p.add_argument("--dry-run", action="store_true")
    r = sub.add_parser("release", help="hand the claimed tasks back to the queue")
    r.add_argument("tasks", nargs="*", help="default: every claim `tk.sh claims` reports")
    r.add_argument("--epic", default=None, help="the epic whose view to regenerate and whose name goes in the commit")
    g = r.add_mutually_exclusive_group()
    g.add_argument("--keep-uncommitted", action="store_true", help="(default) leave a REATTACH worktree in place")
    g.add_argument("--drop-uncommitted", action="store_true", help="remove a REATTACH worktree after its work is preserved, so the next dispatch is FRESH")
    r.add_argument("--no-push", dest="push", action="store_false")
    r.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    try:
        project = load()
    except ProjectError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return EXIT_USAGE
    cwd = str(REPO)
    if args.form == "assess":
        results, _ = assess(None, cwd)
        print(report("assess", results, EXIT_OK))
        return EXIT_OK
    if args.form == "pause":
        results, code = pause(args.epic, push=args.push, dry_run=args.dry_run, project=project, runner=None, cwd=cwd)
    else:
        results, code = release(args.tasks, epic=args.epic, drop_uncommitted=args.drop_uncommitted, push=args.push, dry_run=args.dry_run, project=project, runner=None, cwd=cwd)
    print(report(args.form, results, code))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
