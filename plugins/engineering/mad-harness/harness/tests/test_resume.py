"""`resume_point()` — where a task's work already is, and at what point to adopt it.

Every state here corresponds to a way a run can be stopped after a worker has started.
A wrong answer in the FRESH direction is the bug this exists to end: the task is
re-implemented beside the branch that already holds it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from models import resume


def _git(cwd: Path, *args: str, date: str | None = None) -> str:
    env = dict(os.environ, GIT_AUTHOR_DATE=date or "2026-09-16T10:00:00", GIT_COMMITTER_DATE=date or "2026-09-16T10:00:00")
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=cwd, capture_output=True, text=True, check=True, env=env,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "README.md").write_text("base\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "init")
    return r


def _branch(repo: Path, name: str, *subjects: str, date: str | None = None, worktree: bool = False) -> str:
    """A worker branch with one commit per subject; optionally left with a live worktree."""
    if worktree:
        wt = repo / ".claude" / "worktrees" / name
        _git(repo, "worktree", "add", "-q", "-b", name, str(wt))
        where = wt
    else:
        _git(repo, "switch", "-q", "-c", name)
        where = repo
    for i, subject in enumerate(subjects):
        (where / f"{name}-{i}.txt").write_text("work\n")
        _git(where, "add", "-A")
        _git(where, "commit", "-q", "-m", subject, date=date)
    if not worktree:
        _git(repo, "switch", "-q", "main")
    return _git(repo, "rev-parse", name)


def test_nothing_is_fresh(repo):
    assert resume.resume_point("T-1", repo, notes="").state == "FRESH"


def test_committed_unmerged_work_with_no_verdict_is_verify(repo):
    _branch(repo, "harness-w1-T-1", "feat: half of it [T-1]", "feat: the rest [T-1]")
    rp = resume.resume_point("T-1", repo, notes="")
    assert (rp.state, rp.branch, rp.commits, rp.dirty) == ("VERIFY", "harness-w1-T-1", 2, False)
    assert "--resume harness-w1-T-1" in rp.describe()


def test_a_recorded_pass_at_this_head_is_merge_and_at_an_older_head_is_not(repo):
    head = _branch(repo, "harness-w1-T-1", "feat: done [T-1]")
    assert resume.resume_point("T-1", repo, notes=f"L1 PASS\nVERIFIED {head[:10]}: L1 · L2 · L3").state == "MERGE"
    assert resume.resume_point("T-1", repo, notes="VERIFIED 0123456789ab").state == "VERIFY", "a verdict for another head is not this head's"


def test_uncommitted_work_in_a_live_worktree_is_reattach_and_beats_everything(repo):
    _branch(repo, "harness-w1-T-1", "feat: done [T-1]", worktree=True)
    wt = repo / ".claude" / "worktrees" / "harness-w1-T-1"
    (wt / "half.py").write_text("def f(): ...\n")
    head = _git(repo, "rev-parse", "harness-w1-T-1")
    rp = resume.resume_point("T-1", repo, notes=f"VERIFIED {head}")
    assert rp.state == "REATTACH" and rp.worktree == str(wt) and rp.dirty
    assert "--resume harness-w1-T-1" in rp.describe()


def test_harness_residue_in_a_worktree_is_not_uncommitted_work(repo):
    _branch(repo, "harness-w1-T-1", "feat: done [T-1]", worktree=True)
    wt = repo / ".claude" / "worktrees" / "harness-w1-T-1"
    (wt / ".swarm-env").write_text("export DB=x\n")
    (wt / ".swarm").mkdir()
    (wt / ".swarm" / "commitmsg.txt").write_text("x\n")
    assert resume.resume_point("T-1", repo, notes="").state == "VERIFY"


def test_work_that_already_landed_is_merged_not_fresh(repo):
    _branch(repo, "worktree-agent-a1b2c3", "feat: done [T-1]")
    _git(repo, "merge", "-q", "--no-edit", "worktree-agent-a1b2c3")
    rp = resume.resume_point("T-1", repo, notes="")
    assert rp.state == "MERGED" and rp.merged and rp.commits == 0


def test_a_branch_cut_for_the_task_but_never_used_is_fresh(repo):
    _git(repo, "branch", "harness-w2-T-1")
    assert resume.resume_point("T-1", repo, notes="").state == "FRESH"


def test_claude_code_worktree_agent_refs_are_found_by_their_commits(repo):
    _branch(repo, "worktree-agent-9f8e7d", "fix(scoring): wire it. Closes T-1.")
    rp = resume.resume_point("T-1", repo, notes="")
    assert rp.state == "VERIFY" and rp.branch == "worktree-agent-9f8e7d"


def test_id_matching_is_exact_a_sibling_id_is_not_this_task(repo):
    _branch(repo, "harness-w1-T-10", "feat: other [T-10]")
    _branch(repo, "harness-w1-T-1x", "feat: other [T-1.2]")
    assert resume.resume_point("T-1", repo, notes="").state == "FRESH"


def test_the_newest_unmerged_branch_is_the_resume_point_and_the_rest_are_listed(repo):
    _branch(repo, "worktree-agent-old", "feat: attempt 1 [T-1]", date="2026-09-10T10:00:00")
    _branch(repo, "worktree-agent-new", "feat: attempt 2 [T-1]", date="2026-09-15T10:00:00")
    rp = resume.resume_point("T-1", repo, notes="")
    assert rp.branch == "worktree-agent-new" and rp.others == ("worktree-agent-old",)
    assert "worktree-agent-old" in rp.describe()
