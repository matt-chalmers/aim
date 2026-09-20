"""The tracker-sync tail — export, render, commit, pull, push, autosync — stated once.

THREE COPIES OF ONE SEQUENCE. `/swarm` step 9, `/grind` §10 and `/halt` §4 each spelled
out the same eight to ten shell lines for the orchestrator to run one per tool call:
`tk.sh export` → `render-epic.sh` → `git add <the tracked export>` → `git commit` →
`git pull --rebase` → `git push` → `git status -sb` → `tk.sh autosync on`. Each copy
carried the same warnings — "`export` is load-bearing, not optional", "in this order,
every time" — because each was a place the orchestrator could skip a line, and the recorded
incidents are exactly those skips: a backlog published showing a closed record as
`in_progress` (export skipped; `git status` looked clean because the on-disk file matched
HEAD), and `export.auto` left off for a whole session (autosync skipped). Two of the three
copies also said `git pull --rebase`, which `close_epic.py` had already recorded refuses to
start on a beads repo after `autosync off`. `close_epic.py` (e)-(h) was the fourth copy,
in code; this module is that code, extracted, and the other three call it.

MEASURED, as for the rest of the collapsed sequences: an orchestrator's context averaged
~380k tokens over a 237-request campaign, so each of these lines run as its own tool call
re-read that context to learn an exit status — ~$0.11-0.17 per line, ~6x a worker's rate.

THE ORDER IS THE CODE. Export before add (or the commit carries a stale artefact); render
before add (or the view is a commit behind); pull before push (or the push is refused);
autosync last (it is the step that re-enables the backend writing on its own, and it must
not happen before this actor's writes are committed). A failure stops the sequence and the
report says what remains, by hand, in order — as `close_epic.py` did.

UPSTREAM MOVED IS A DECISION, NOT A DETAIL. `git pull --rebase` can carry in commits from
another actor. `/swarm` step 9 says: if the rebase pulled in someone else's commits, re-run
the wave gate before pushing. The sequence cannot run a wave gate — it does not know the
lanes — so with `stop_if_upstream_moved` it counts the upstream commits the pull brought
in (`@{u}` before and after) and STOPS before the push when there were any, with the push
and the autosync restore listed as remaining. The orchestrator re-gates, then pushes. An
epic close passes `False`: its commit is the tracker export alone, and nothing about it
depends on the code the rebase brought in.
"""

from __future__ import annotations

import json
from pathlib import Path

from .resolve import HARNESS, REPO
from .steps import (
    FAIL,
    INFO,
    OK,
    REMOTE_TIMEOUT,
    Raw,
    Result,
    execute,
    failure_detail,
    tail,
)

TK = HARNESS / "tracker" / "tk.sh"
RENDER = HARNESS / "tracker" / "render-epic.sh"


def _ok_detail(raw: Raw) -> str:
    return tail(raw.stdout, 1) or tail(raw.stderr, 1) or "ok"


def export_path(runner, cwd: str) -> tuple[str | None, Raw]:
    """What the backend declares as its tracked artefact. None is an answer: the backend
    keeps no tracked export, and the commit step says so rather than adding nothing.

    NOT `owned_paths`: for beads that prefix also holds `config.yaml`, which `autosync
    off` rewrote at pre-flight — committing it would record `export.auto: false` and dirty
    the tree again the moment the restore runs.
    """
    raw = execute([str(TK), "backend", "--json"], cwd=cwd, runner=runner)
    if not raw.ran or raw.returncode != 0:
        return None, raw
    try:
        caps = json.loads(raw.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None, Raw(None, raw.stdout, raw.stderr, error=f"backend --json did not answer in JSON:\n{tail(raw.stdout)}")
    path = caps.get("export_path")
    return (str(path) if path else None), raw


def view_targets(epics: list[str], project, cwd: str) -> list[tuple[str, Path | None, list[str]]]:
    """Per epic: its staging folder's `tasks.md` (project-relative), or None with the
    spellings tried. An epic with no staging folder has no view to regenerate — that is
    an answer, reported on the line, never a failure."""
    from tracker.staging import known_prefix, staged_folder

    proposed = (getattr(project, "paths", None) or {}).get("proposed") if project else None
    out: list[tuple[str, Path | None, list[str]]] = []
    if not proposed:
        return [(e, None, ["(no paths.proposed declared)"]) for e in epics]
    for epic in epics:
        folder, tried = staged_folder(epic, Path(cwd) / proposed, known_prefix())
        out.append((epic, (folder / "tasks.md").relative_to(cwd) if folder else None, tried))
    return out


def by_hand(
    message: str, export: str | None, views: list[str], extra: list[str], push: bool, restore_autosync: bool
) -> list[tuple[str, str]]:
    """(step, command) for every write, as the orchestrator would type it. A stopped
    run's "remaining" list is a suffix of this."""
    quoted = message.replace('"', '\\"')
    paths = [p for p in ([export] if export else []) + views + extra]
    steps = [("export", f"{TK} export")]
    steps += [("render", f"{RENDER} <epic> --write {v}") for v in views]
    steps.append(("commit", f'git add -- {" ".join(paths)} && git commit -m "{quoted}"' if paths else "(no tracked export — nothing to commit)"))
    if push:
        steps += [("pull", "git pull --rebase --autostash"), ("push", "git push"), ("status", "git status -sb")]
    if restore_autosync:
        steps.append(("autosync", f"{TK} autosync on"))
    return steps


def sync(
    *,
    message: str,
    epics: list[str] = (),
    export: str | None,
    extra_paths: list[str] = (),
    push: bool = True,
    stop_if_upstream_moved: bool = False,
    restore_autosync: bool = False,
    project=None,
    runner=None,
    cwd: str | None = None,
) -> list[Result]:
    """Export → render each epic's view → add → commit → pull → push → status → autosync,
    stopping at the first failure. The failing result carries what remains.

    :param export: the tracked export path the backend declared (`export_path()`), or
        None for a backend that keeps none. Resolved by the caller BEFORE its own writes,
        so a backend that cannot answer stops the caller before anything is written.
    :param extra_paths: more paths to stage with the export — an archived staging folder,
        a repaired `harness.yaml`.
    :param restore_autosync: `tk.sh autosync on` at the end. Off by default — the safe
        side is not to touch what this call does not own. A run that turned it off at
        its own pre-flight (an epic close, a standalone /swarm or /grind) passes True; a
        wave inside a campaign does not, and §5 restores it.
    """
    at = cwd or str(REPO)
    results: list[Result] = []
    epics = list(epics)
    targets = view_targets(epics, project, at) if epics else []
    views = [str(v) for _, v, _ in targets if v is not None]
    plan = by_hand(message, export, views, list(extra_paths), push, restore_autosync)
    names = [s for s, _ in plan]

    def fail(step: str, name: str, detail: str, raw: Raw | None = None) -> list[Result]:
        results.append(Result(name, FAIL, detail, raw, remaining=[c for s, c in plan if names.index(s) >= names.index(step)]))
        return results

    # export — ALWAYS writes. `bd export` without `-o` streamed to stdout and wrote
    # nothing; the tracked file then showed a closed record as in_progress while
    # `git status` looked clean. The shim removed that trap; this step keeps it removed.
    raw = execute([str(TK), "export"], cwd=at, runner=runner)
    if not raw.ran or raw.returncode != 0:
        return fail("export", "tk.sh export", failure_detail(raw), raw)
    results.append(Result("tk.sh export", OK, "tracked export regenerated", raw))

    # render — the epic's readable view, GENERATED, beside the export it reflects.
    for epic, view, tried in targets:
        name = f"render-epic.sh {epic}"
        if view is None:
            results.append(Result(name, OK, f"no staging folder for {epic} ({', '.join(tried)}); nothing to render"))
            continue
        # `--check` first: the view is GENERATED, and a copy somebody hand-edited is a
        # second source of truth. Regenerating would silently overwrite the evidence, so
        # the drift is read before the write and said on the line — then overwritten,
        # which is the right end state either way (edited, or stale since the last wave).
        probe = execute([str(RENDER), epic, "--check", "--write", str(view)], cwd=at, runner=runner)
        drifted = probe.ran and probe.returncode != 0 and (Path(at) / view).exists()
        raw = execute([str(RENDER), epic, "--write", str(view)], cwd=at, runner=runner)
        if not raw.ran or raw.returncode != 0:
            return fail("render", name, failure_detail(raw), raw)
        note = f"{view} — had drifted (hand-edited, or stale since the last wave); regenerated" if drifted else str(view)
        results.append(Result(name, OK, note, raw))

    # commit — the export, the views, and whatever the caller staged deliberately.
    paths = ([export] if export else []) + views + list(extra_paths)
    if not paths:
        results.append(Result("git commit", OK, "the backend declares no tracked export — nothing to commit"))
    else:
        name = f"git add {' '.join(paths)} && git commit"
        raw = execute(["git", "add", "--", *paths], cwd=at, runner=runner)
        if not raw.ran or raw.returncode != 0:
            return fail("commit", name, failure_detail(raw), raw)
        staged = execute(["git", "diff", "--cached", "--quiet"], cwd=at, runner=runner)
        if not staged.ran or staged.returncode not in (0, 1):
            return fail("commit", name, failure_detail(staged), staged)
        if staged.returncode == 0:
            results.append(Result(name, OK, "nothing staged — the export already matched HEAD; no commit made", raw))
        else:
            raw = execute(["git", "commit", "-m", message], cwd=at, runner=runner)
            if not raw.ran or raw.returncode != 0:
                return fail("commit", name, failure_detail(raw), raw)
            results.append(Result(name, OK, f"{message} — {_ok_detail(raw)}", raw))

    if push:
        before = execute(["git", "rev-parse", "--verify", "--quiet", "@{u}"], cwd=at, runner=runner)
        upstream_before = before.stdout.strip() if before.ran and before.returncode == 0 else ""
        raw = execute(["git", "pull", "--rebase", "--autostash"], cwd=at, timeout=REMOTE_TIMEOUT, runner=runner)
        if not raw.ran or raw.returncode != 0:
            return fail("pull", "git pull --rebase --autostash", failure_detail(raw), raw)
        pulled = 0
        if upstream_before:
            after = execute(["git", "rev-parse", "--verify", "--quiet", "@{u}"], cwd=at, runner=runner)
            if after.ran and after.returncode == 0 and after.stdout.strip() != upstream_before:
                count = execute(["git", "rev-list", "--count", f"{upstream_before}..{after.stdout.strip()}"], cwd=at, runner=runner)
                pulled = int(count.stdout.strip() or 0) if count.ran and count.returncode == 0 else -1
        if pulled and stop_if_upstream_moved:
            how = f"{pulled} upstream commit(s)" if pulled > 0 else "upstream commits (count unknown)"
            return fail(
                "push", "git pull --rebase --autostash",
                f"pulled in {how} from another actor. Not pushing over them unseen: re-run the "
                f"wave gate on the rebased tree, then the remaining steps.", raw,
            )
        results.append(Result("git pull --rebase --autostash", OK, (f"{pulled} upstream commit(s) pulled in — " if pulled else "") + _ok_detail(raw), raw))
        raw = execute(["git", "push"], cwd=at, timeout=REMOTE_TIMEOUT, runner=runner)
        if not raw.ran or raw.returncode != 0:
            return fail("push", "git push", failure_detail(raw), raw)
        results.append(Result("git push", OK, _ok_detail(raw), raw))
        raw = execute(["git", "status", "-sb"], cwd=at, runner=runner)
        # The FIRST line is the branch line (`## main...origin/main [ahead 1]`); the rest
        # are paths, and a dirty tree here is somebody else's business, not this step's.
        head = (raw.stdout.splitlines() or [""])[0].strip() if raw.ran else ""
        if not raw.ran or raw.returncode != 0 or "[ahead" in head or "[behind" in head:
            return fail("status", "git status -sb", f"not up to date with origin after the push:\n{head or failure_detail(raw)}", raw)
        results.append(Result("git status -sb", OK, head or "up to date", raw))
    else:
        results.append(Result("git pull / git push", OK, "skipped (--no-push) — push by hand: git pull --rebase --autostash && git push"))

    if restore_autosync:
        raw = execute([str(TK), "autosync", "on"], cwd=at, runner=runner)
        if not raw.ran or raw.returncode != 0:
            return fail("autosync", "tk.sh autosync on", failure_detail(raw), raw)
        results.append(Result("tk.sh autosync on", OK, "restored — what pre-flight disabled", raw))
    else:
        results.append(Result("tk.sh autosync on", INFO, "left off — a larger run still owns the tracker and restores it at its close"))
    return results
