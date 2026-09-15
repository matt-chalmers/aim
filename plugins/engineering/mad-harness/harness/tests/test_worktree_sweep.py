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


def _sweep(primary: Path) -> str:
    proc = subprocess.run(
        [str(SWEEP), "--min-age", "0"],
        cwd=primary, capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "MAIN_BRANCH": "main"},
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


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
