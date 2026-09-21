"""A worker's commit — one task, one commit, inside the merge slot — as one call.

WHAT THIS REPLACES. Both writers carried the same five-step sequence in prose:
`slot-acquire` → `git status --porcelain` (assert only your paths are dirty) →
`git add <explicit paths>` → `git commit` → `slot-release`, with "if a path you don't own
is staged, release the slot and return FAIL contaminated index" and "never commit the
tracker's export". Duplicated in two agents, and `quality-engineer` spelled the release as
`slot-acquire release` — not a verb — so its slot stayed held until `STALE_AFTER_S`. A
commit that failed mid-sequence left the slot held too; nothing times it out.

THE SLOT IS RELEASED IN A `finally`, the index is checked BEFORE the slot is taken (a
contaminated index is a refusal, not a mutex holder), the export path the backend
declares is refused (the orchestrator syncs it once per wave), and the paths are staged
explicitly — never `-A`, never `.`. Exit 0 committed · 1 refused (contaminated index, the
export, nothing to commit, the slot held) · 2 usage.
"""

from __future__ import annotations

import os
import sys

from .resolve import CHECKOUT, HARNESS
from .steps import FAIL, INFO, OK, Result, execute, failure_detail, render

TK = HARNESS / "tracker" / "tk.sh"


def _actor() -> str:
    return os.environ.get("TRACKER_ACTOR") or os.environ.get("BEADS_ACTOR") or f"commit-{os.getpid()}"


def dirty_paths(runner, cwd: str) -> tuple[list[str], list[str]]:
    """(paths with changes, paths that are untracked) from `git status --porcelain`."""
    raw = execute(["git", "status", "--porcelain", "--untracked-files=all"], cwd=cwd, runner=runner)
    changed, untracked = [], []
    for ln in (raw.stdout if raw.ran else "").splitlines():
        if len(ln) < 4:
            continue
        code, path = ln[:2], ln[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        (untracked if code == "??" else changed).append(path)
    return changed, untracked


def _residue(path: str) -> bool:
    return path.startswith((".swarm", "__pycache__/", ".venv/", "node_modules/", ".harness/")) or path.endswith(".pyc")


def run(task: str, message: str, paths: list[str], *, runner=None, cwd: str | None = None, export: str | None = None) -> tuple[str, int]:
    # THE CHECKOUT THE WORKER STANDS IN — its worktree — not the harness directory the
    # wrapper cd'd into before Python started. `resolve.CHECKOUT` reads the caller's pwd.
    at = cwd or str(CHECKOUT)
    results: list[Result] = []
    holder = _actor()

    changed, untracked = dirty_paths(runner, at)
    mine = set(paths)
    # THE TRACKER'S EXPORT IS RESIDUE, NOT CONTAMINATION. beads' hooks stage the export on
    # every write, so a worker's index carries `M  .beads/issues.jsonl` it never touched;
    # refusing that as "contaminated index — never reset" left the worker unable to
    # commit at all. It is unstaged here (the file is left as it is) and never named.
    if export:
        staged_export = [p for p in changed if (p == export or p.startswith(export.rstrip("/") + "/")) and p not in paths]
        if staged_export:
            execute(["git", "reset", "-q", "--", *staged_export], cwd=at, runner=runner)
            changed = [p for p in changed if p not in staged_export]
            results.append(Result("tracker export", INFO, f"unstaged, not yours to commit: {', '.join(staged_export)}"))
    foreign = [p for p in changed if p not in mine and not _residue(p)]
    if foreign:
        results.append(Result("git status --porcelain", FAIL, "contaminated index — paths you did not name are modified or staged; a sibling's work, or yours unnamed:\n" + "\n".join(f"  {p}" for p in foreign[:10]) + "\nName every path you changed, or FAIL contaminated index. Never stash, checkout or reset them."))
        return _report(task, results), 1
    if export and any(p == export or p.startswith(export.rstrip("/") + "/") for p in paths):
        results.append(Result("the tracked export", FAIL, f"{export} is in your paths — never commit the tracker's export; the orchestrator syncs it once per wave"))
        return _report(task, results), 1
    missing = [p for p in paths if p not in changed and p not in untracked]
    if missing:
        results.append(Result("git status --porcelain", FAIL, "named paths with no change to commit:\n" + "\n".join(f"  {p}" for p in missing)))
        return _report(task, results), 1
    results.append(Result("git status --porcelain", OK, f"{len(paths)} path(s), all yours"))

    raw = execute([str(TK), "slot-acquire", "--holder", holder], cwd=at, runner=runner)
    if not raw.ran or raw.returncode != 0:
        results.append(Result("tk.sh slot-acquire", FAIL, f"the merge slot is held — a sibling is committing; try again shortly:\n{failure_detail(raw)}", raw))
        return _report(task, results), 1
    results.append(Result("tk.sh slot-acquire", OK, holder, raw))
    try:
        raw = execute(["git", "add", "--", *paths], cwd=at, runner=runner)
        if not raw.ran or raw.returncode != 0:
            results.append(Result("git add", FAIL, failure_detail(raw), raw))
            return _report(task, results), 1
        results.append(Result(f"git add -- {' '.join(paths[:4])}{' …' if len(paths) > 4 else ''}", OK, "staged, explicitly", raw))
        raw = execute(["git", "commit", "-m", message], cwd=at, runner=runner)
        if not raw.ran or raw.returncode != 0:
            results.append(Result("git commit", FAIL, failure_detail(raw), raw))
            return _report(task, results), 1
        sha = execute(["git", "rev-parse", "--short=12", "HEAD"], cwd=at, runner=runner)
        results.append(Result("git commit", OK, f"{sha.stdout.strip() if sha.ran else '?'}  {message}", raw))
    finally:
        rel = execute([str(TK), "slot-release", "--holder", holder], cwd=at, runner=runner)
        results.append(Result("tk.sh slot-release", OK if rel.ran and rel.returncode == 0 else FAIL, "released" if rel.ran and rel.returncode == 0 else failure_detail(rel), rel))
    return _report(task, results), (1 if any(r.status == FAIL for r in results) else 0)


def _report(task: str, results: list[Result]) -> str:
    out = [f"commit {task}", render(results)]
    if any(r.status == FAIL for r in results):
        out.append("\nNOT COMMITTED — read the [FAIL] line. The slot is not held.")
    else:
        out.append("\nCOMMITTED — one task, one commit. Do not push; close the task and return.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="commit.sh", description="One task, one commit, inside the merge slot: the index checked for paths you did not name, the slot taken, your paths staged explicitly, the commit made, the slot released in a finally.")
    ap.add_argument("task")
    ap.add_argument("-m", "--message", required=True, help="conventional, naming the task id")
    ap.add_argument("paths", nargs="+", help="every path you changed — after `--`")
    args = ap.parse_args(argv)
    if args.task not in args.message:
        print(f"the message must name the task id ({args.task}): a lens judging your work finds it there", file=sys.stderr)
        return 2
    export = None
    try:
        import tracker

        export = tracker.task_store().capabilities().export_path
    except Exception:  # noqa: BLE001 — no tracker, no export to refuse
        export = None
    text, code = run(args.task, args.message, args.paths, export=export)
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
