"""`worktree-sweep.sh` against real worktrees.

The sweep refuses a DIRTY worktree — correctly, and forever. What counts as dirt is
therefore the whole question. It used to enumerate the harness's own residue by name
(`.swarm-env`), so a worktree holding scratch an EARLIER version wrote (`.swarm-pytest.env`,
`.swarm/`) could never be reclaimed by any later sweep: two of the three stranded worktrees
behind the 35-worktree incident were held by nothing else.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from models.resolve import HARNESS

SWEEP = HARNESS / "swarm" / "worktree-sweep.sh"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout


@pytest.fixture
def repo_with_worktree(tmp_path) -> tuple[Path, Path]:
    primary = tmp_path / "primary"
    primary.mkdir()
    _git(primary, "init", "-q", "-b", "main")
    (primary / "README.md").write_text("hello\n")
    _git(primary, "add", "-A")
    _git(primary, "commit", "-q", "-m", "init")
    wt = primary / ".claude" / "worktrees" / "w1"
    _git(primary, "worktree", "add", "-q", "-b", "harness-w1", str(wt))
    return primary, wt


def _env() -> dict[str, str]:
    """An operator's environment: no MAD_HARNESS_* pins, no test venv on PATH."""
    import os

    env = {k: v for k, v in os.environ.items() if not k.startswith("MAD_HARNESS_")}
    env["PATH"] = os.pathsep.join(d for d in env["PATH"].split(os.pathsep) if "/.venv/" not in d)
    env.pop("VIRTUAL_ENV", None)
    env["MAIN_BRANCH"] = "main"
    return env


def _sweep(primary: Path, *flags: str) -> str:
    proc = subprocess.run(
        [str(SWEEP), "--min-age", "0", *flags],
        cwd=primary, capture_output=True, text=True, timeout=180, env=_env(),
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


TK = HARNESS / "tracker" / "tk.sh"
TRACKED_CONFIG = """\
name: Consumer
slug: consumer
stacks: []
areas: []
paths: {}
testing: {layout: {}}
tracker: {backend: mdfiles, dir: .harness/tasks, export: docs/tasks}
"""


@pytest.fixture
def tracked_repo(tmp_path) -> Path:
    """A primary checkout with a real mdfiles tracker and no worktrees."""
    primary = tmp_path / "primary"
    primary.mkdir()
    _git(primary, "init", "-q", "-b", "main")
    (primary / "harness.yaml").write_text(TRACKED_CONFIG)
    (primary / ".gitignore").write_text(".harness/\n")
    _git(primary, "add", "-A")
    _git(primary, "commit", "-q", "-m", "init")
    return primary


def _task(primary: Path, title: str) -> str:
    return subprocess.run(
        [str(TK), "create", title], cwd=primary, capture_output=True, text=True,
        timeout=120, env=_env(), check=True,
    ).stdout.strip().splitlines()[-1]


def _orphan(primary: Path, name: str, subject: str) -> None:
    """A worker branch with one commit and NO worktree — what the sweep's own
    'committed, unmerged: remove the worktree, keep the ref' path leaves behind."""
    _git(primary, "switch", "-q", "-c", name)
    (primary / f"{name}.txt").write_text("work\n")
    _git(primary, "add", "-A")
    _git(primary, "commit", "-q", "-m", subject)
    _git(primary, "switch", "-q", "main")


def _count(out: str, label: str) -> int:
    line = next(ln for ln in out.splitlines() if label in ln)
    return int(line.rsplit(":", 1)[1])


def test_residue_from_any_harness_version_never_makes_a_worktree_dirty(repo_with_worktree):
    """The two files found holding stranded worktrees in the field, plus the one the
    current version writes. None of them is work."""
    primary, wt = repo_with_worktree
    (wt / ".swarm-env").write_text("export DB_NAME=x_w1\n")
    (wt / ".swarm-pytest.env").write_text("DB_NAME=x_w1\n")
    (wt / ".swarm").mkdir()
    (wt / ".swarm" / "commitmsg.txt").write_text("wip\n")
    out = _sweep(primary)
    assert _count(out, "DIRTY, left alone") == 0, out
    assert "would remove" in out, out


def test_an_untracked_source_file_is_still_dirt(repo_with_worktree):
    """Widening the residue pattern must not widen it to WORK."""
    primary, wt = repo_with_worktree
    (wt / "notes.md").write_text("half-written\n")
    out = _sweep(primary)
    assert _count(out, "DIRTY, left alone") == 1, out
    assert "notes.md" in out


def test_a_tracked_modification_is_dirt_even_beside_residue(repo_with_worktree):
    primary, wt = repo_with_worktree
    (wt / ".swarm-env").write_text("x\n")
    (wt / "README.md").write_text("changed\n")
    out = _sweep(primary)
    assert _count(out, "DIRTY, left alone") == 1, out



# --- pass 2: refs with no directory --------------------------------------------------------


def test_an_orphaned_ref_for_an_open_task_is_reported_in_flight_and_never_deleted(tracked_repo):
    """Fifteen of these sat in a repository while every sweep reported clean. The work is
    committed, tested, and held by nobody; re-dispatching the task starts from scratch."""
    tid = _task(tracked_repo, "wire the thing")
    _orphan(tracked_repo, "harness-w1-wire", f"feat: wire the thing [{tid}]")
    out = _sweep(tracked_repo)
    assert "IN FLIGHT (task still open)       : 1" in out, out
    assert f"harness-w1-wire  ({tid})" in out
    _sweep(tracked_repo, "--apply", "--prune-orphans")
    assert "harness-w1-wire" in _git(tracked_repo, "branch", "--list"), "in-flight work was deleted"


def test_a_stale_orphan_is_kept_by_default_and_pruned_only_on_request(tracked_repo):
    tid = _task(tracked_repo, "done elsewhere")
    subprocess.run([str(TK), "close", tid, "--reason", "landed by hand"], cwd=tracked_repo,
                   capture_output=True, text=True, timeout=120, env=_env(), check=True)
    _orphan(tracked_repo, "worktree-agent-abc123", f"feat: done elsewhere [{tid}]")
    out = _sweep(tracked_repo)
    assert "STALE (every task closed)         : 1" in out, out
    _sweep(tracked_repo, "--apply")
    assert "worktree-agent-abc123" in _git(tracked_repo, "branch", "--list"), "--apply alone must not prune"
    _sweep(tracked_repo, "--apply", "--prune-orphans")
    assert "worktree-agent-abc123" not in _git(tracked_repo, "branch", "--list")


def test_a_merged_orphan_ref_is_deleted_on_apply(tracked_repo):
    tid = _task(tracked_repo, "merged")
    _orphan(tracked_repo, "harness-w2-merged", f"feat: merged [{tid}]")
    _git(tracked_repo, "merge", "-q", "--no-edit", "harness-w2-merged")
    out = _sweep(tracked_repo)
    assert "would delete (merged, no worktree):       harness-w2-merged" in out, out
    _sweep(tracked_repo, "--apply")
    assert "harness-w2-merged" not in _git(tracked_repo, "branch", "--list")


def test_a_ref_with_no_task_id_is_unknown_and_kept(tracked_repo):
    _task(tracked_repo, "so the tracker has an id shape to look for")
    _orphan(tracked_repo, "worktree-agent-noid", "chore: no id anywhere")
    out = _sweep(tracked_repo, "--apply", "--prune-orphans")
    assert "UNKNOWN (no task id in its log)   : 1" in out, out
    assert "worktree-agent-noid" in _git(tracked_repo, "branch", "--list")


def test_a_branch_that_still_has_a_worktree_is_not_an_orphan(repo_with_worktree):
    primary, wt = repo_with_worktree
    out = _sweep(primary)
    assert "IN FLIGHT (task still open)       : 0" in out and "UNKNOWN (no task id in its log)   : 0" in out, out
