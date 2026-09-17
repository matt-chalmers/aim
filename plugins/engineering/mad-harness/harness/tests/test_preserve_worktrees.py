"""`preserve-worktrees.sh` — a halt keeps the work before it removes anything."""

from __future__ import annotations

import subprocess
from pathlib import Path

from models.resolve import HARNESS

SCRIPT = HARNESS / "swarm" / "preserve-worktrees.sh"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@x", "-c", "user.name=t", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_uncommitted_untracked_and_unmerged_work_are_all_written_out(tmp_path):
    primary = tmp_path / "p"
    primary.mkdir()
    _git(primary, "init", "-q", "-b", "main")
    (primary / "a.py").write_text("x = 1\n")
    _git(primary, "add", "-A")
    _git(primary, "commit", "-q", "-m", "init")
    wt = primary / ".claude" / "worktrees" / "w1"
    _git(primary, "worktree", "add", "-q", "-b", "harness-w1-T-1", str(wt))
    (wt / "b.py").write_text("committed\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "feat: half [T-1]")
    (wt / "a.py").write_text("x = 2\n")            # uncommitted edit
    (wt / "new.py").write_text("untracked\n")      # untracked file
    (wt / ".swarm-env").write_text("residue\n")    # harness residue, not work

    out = subprocess.run([str(SCRIPT), "--to", ".harness/halted-test"], cwd=primary, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
    assert out.returncode == 0, out.stderr
    dest = primary / ".harness" / "halted-test" / "harness-w1-T-1"
    assert "x = 2" in (dest / "unstaged.diff").read_text()
    assert (dest / "untracked" / "new.py").read_text() == "untracked\n"
    assert not (dest / "untracked" / ".swarm-env").exists(), "residue is not work"
    assert any(p.suffix == ".patch" for p in (dest / "commits").iterdir()), "the unmerged commit is a patch"
    assert "commits ahead: 1" in out.stdout and "1 worktree(s) preserved" in out.stdout


def test_a_clean_worktree_preserves_nothing_and_says_so(tmp_path):
    primary = tmp_path / "p"
    primary.mkdir()
    _git(primary, "init", "-q", "-b", "main")
    (primary / "a.py").write_text("x\n")
    _git(primary, "add", "-A")
    _git(primary, "commit", "-q", "-m", "init")
    _git(primary, "worktree", "add", "-q", "-b", "harness-w1-T-2", str(primary / ".claude" / "worktrees" / "w1"))
    out = subprocess.run([str(SCRIPT), "--to", ".harness/h"], cwd=primary, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
    assert out.returncode == 0 and "nothing to preserve" in out.stdout
    assert not (primary / ".harness" / "h").exists()
