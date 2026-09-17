"""Where a task's work already is, so a resumed run adopts it instead of restarting.

A stoppage after a worker has started — the environment killed mid-wave, a lens still
running, a branch committed but not yet merged — leaves a worktree, a branch, or both.
Nothing looked for them: dispatch always cut a NEW branch from HEAD, the sweep's own rule
removed the old worktree and kept its ref, and the task was re-implemented from scratch
beside the branch that already held it. One task accumulated five such branches; a
repository reached 70, 56 of them unmerged.

This answers one question — "for task T, what exists, and at what point do I adopt it?" —
so that `/swarm` asks it BEFORE dispatching, and `dispatch.sh --resume <branch>` can put a
worker back on its own work.

    state       meaning                                          what to do
    MERGE       committed, lenses recorded PASS at this head     merge it; no worker
    VERIFY      committed, no verdict recorded for this head     run the lenses; PASS → merge, FAIL → --resume
    REATTACH    a worktree holds UNCOMMITTED work                dispatch INTO it (--resume its branch)
    FRESH       nothing to adopt                                 dispatch as normal

There is deliberately NO "merged" state. A branch with nothing ahead of main was either
cut and never used, or fast-forwarded into main — and from the ref alone the two are
indistinguishable. The first version guessed by grepping main's log for the task id, and a
tracker-sync commit that named the id made a zero-commit branch read as MERGED: a killed
worker that had committed nothing was reported as "work landed; close the task". A false
FRESH costs a redundant dispatch that the tracker's own closed status prevents anyway; a
false MERGED closes work nobody did. Zero commits ahead is FRESH, always.

The verdict comes from a `VERIFIED <sha>` note on the task, which the lens step records
when every lens passes. Absent, committed work resumes at VERIFY — the lenses are cheap
next to re-implementing.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from .resolve import REPO

#: The two naming schemes a worker branch can have: this harness's SDK dispatch, and
#: Claude Code's own worktree isolation, which the Agent tool uses.
WORKER_REF_GLOBS = ("refs/heads/harness-w*", "refs/heads/worktree-agent-*")

#: What the lens step records on the task once every lens passes. The sha pins the verdict
#: to a head: a later commit on the branch needs a fresh verdict.
VERIFIED = re.compile(r"\bVERIFIED\s+([0-9a-f]{7,40})\b")


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, timeout=60)
    return proc.stdout if proc.returncode == 0 else ""


def main_branch(repo: Path) -> str:
    ref = _git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD").strip()
    if ref.startswith("origin/"):
        return ref[len("origin/"):]
    for candidate in ("main", "master", "trunk"):
        if _git(repo, "rev-parse", "--verify", "--quiet", candidate).strip():
            return candidate
    return "main"


def _worker_refs(repo: Path) -> list[tuple[str, int]]:
    out = []
    for line in _git(repo, "for-each-ref", "--format=%(refname:short) %(committerdate:unix)", *WORKER_REF_GLOBS).splitlines():
        name, _, when = line.partition(" ")
        if name:
            out.append((name, int(when or 0)))
    return out


def _worktrees(repo: Path) -> dict[str, Path]:
    """branch -> worktree path, for every registered worktree that is on a branch."""
    out: dict[str, Path] = {}
    path: Path | None = None
    for line in _git(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree "):])
        elif line.startswith("branch ") and path is not None:
            out[line[len("branch refs/heads/"):]] = path
    return out


def _residue(name: str) -> bool:
    """The same rule as worktree-sweep.sh: harness scratch is not work."""
    return (
        name.startswith(".swarm") or name.endswith(".pyc")
        or name.startswith(("__pycache__/", ".venv/", "node_modules/"))
    )


def is_dirty(worktree: Path) -> bool:
    for line in _git(worktree, "status", "--porcelain").splitlines():
        if not line.strip():
            continue
        if line.startswith("??") and _residue(line[3:]):
            continue
        return True
    return False


def _names(text: str, task: str) -> bool:
    # A trailing "." continues the id only if a digit follows (`m7j7.19`); a sentence
    # ending in the id must still match.
    return re.search(rf"(?<!\w){re.escape(task)}(?!\w|\.\d)", text) is not None


def _mentions(repo: Path, ref: str, main: str, task: str) -> bool:
    """Do the ref's own commits name the task? Ahead of main when there are any; once
    merged there is nothing ahead, but the ref still points at the worker's last commit
    (never at the merge commit), so its tip is the record."""
    ahead = _git(repo, "log", "--format=%s%n%b", f"{main}..{ref}")
    if ahead.strip():
        return _names(ahead, task)
    return _names(_git(repo, "log", "-5", "--format=%s%n%b", ref), task)


@dataclass(frozen=True)
class ResumePoint:
    task: str
    state: str
    branch: str | None = None
    head: str | None = None
    commits: int = 0
    merged: bool = False
    worktree: str | None = None
    dirty: bool = False
    verified: bool = False
    others: tuple[str, ...] = ()

    def describe(self) -> str:
        lines = [f"task:      {self.task}", f"state:     {self.state}"]
        if self.branch:
            how = f"{self.commits} commit(s) ahead of main" if self.commits else "nothing ahead of main — empty, or already landed"
            lines.append(f"branch:    {self.branch}   ({how})")
        if self.worktree:
            lines.append(f"worktree:  {self.worktree}   ({'UNCOMMITTED changes' if self.dirty else 'clean'})")
        if self.branch and self.commits:
            lines.append(f"verified:  {'PASS recorded at this head' if self.verified else 'no verdict recorded for this head'}")
        if self.others:
            lines.append(f"also:      {', '.join(self.others)}   ← other refs holding work for this task; the sweep lists them")
        lines.append("next:      " + NEXT[self.state].format(branch=self.branch or ""))
        return "\n".join(lines)


NEXT = {
    "MERGE": "merge {branch} in step 8 — no worker needed",
    "VERIFY": "run the lenses on {branch}; PASS → merge in step 8, FAIL → dispatch.sh … --resume {branch}",
    "REATTACH": "dispatch.sh … --worker <n> --resume {branch} — the worker continues in that worktree",
    "FRESH": "dispatch as normal",
}


def resume_point(task: str, repo: Path | None = None, notes: str | None = None) -> ResumePoint:
    """The adoption point for `task`. `notes` is the task's notes text; None reads the tracker."""
    repo = (repo or REPO).resolve()
    main = main_branch(repo)
    worktrees = _worktrees(repo)
    slug = task.replace("/", "-")

    candidates = []
    for ref, when in _worker_refs(repo):
        named = ref.endswith(f"-{slug}") or f"-{slug}-" in ref
        if not (named or _mentions(repo, ref, main, task)):
            continue
        commits = len(_git(repo, "rev-list", f"{main}..{ref}").split())
        head = _git(repo, "rev-parse", ref).strip()
        wt = worktrees.get(ref)
        dirty = bool(wt and wt.is_dir() and is_dirty(wt))
        candidates.append((ref, when, commits, head, wt, dirty))

    if not candidates:
        return ResumePoint(task=task, state="FRESH")

    if notes is None:
        notes = _task_notes(task)
    verified_shas = set(VERIFIED.findall(notes or ""))

    # Uncommitted work is unique — it exists nowhere else — so a dirty worktree wins;
    # then the most recently committed unmerged branch; then whatever is left.
    candidates.sort(key=lambda c: (c[5], c[2] > 0, c[1]), reverse=True)
    ref, when, commits, head, wt, dirty = candidates[0]
    others = tuple(c[0] for c in candidates[1:])
    verified = any(head.startswith(s) for s in verified_shas)

    if dirty:
        state = "REATTACH"
    elif commits > 0:
        state = "MERGE" if verified else "VERIFY"
    else:
        state = "FRESH"  # nothing ahead of main: nothing to adopt, whatever the history says

    return ResumePoint(
        task=task, state=state, branch=ref, head=head[:12] if head else None, commits=commits,
        merged=False, worktree=str(wt) if wt else None, dirty=dirty, verified=verified, others=others,
    )


def _task_notes(task: str) -> str:
    try:
        import tracker

        t = tracker.task_store().show(task)
        return t.notes if t else ""
    except Exception:  # noqa: BLE001 — an unreadable tracker means "no verdict", never a crash
        return ""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    if len(args) != 1:
        print("usage: resume-point.sh <task-id> [--json]", file=sys.stderr)
        return 2
    rp = resume_point(args[0])
    print(json.dumps(asdict(rp)) if as_json else rp.describe())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
