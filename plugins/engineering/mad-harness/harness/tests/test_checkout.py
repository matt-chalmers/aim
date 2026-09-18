"""REPO is the project; CHECKOUT is the working tree the caller stands in. They were one.

A dispatched worker stands in its own worktree. With the two collapsed, `run.sh` ran the
worker's tests in the PRIMARY checkout — green with the worker's code deliberately broken —
never loaded its `.swarm-env`, and `peek.sh` showed it the primary's copy of a file it had
just edited. A lab worker caught it, repeated it twice, and filed the bug.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from models import commands as cmd
from models.project import Stack
from models.resolve import HARNESS

PLUGIN = HARNESS.parent


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@x", "-c", "user.name=t", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_the_dispatcher_hands_a_worker_the_project_but_not_its_own_directory(monkeypatch):
    from models import dispatch as mod
    from models.resolve import resolve

    monkeypatch.setattr("models.levers._project_block", lambda: {})
    env = mod.build_env(resolve("verifier"), base={"MAD_HARNESS_CALLER_PWD": "/the/dispatchers/cwd"})
    assert "MAD_HARNESS_CALLER_PWD" not in env, "inherited, every wrapper the worker calls keeps it"
    assert env["MAD_HARNESS_REPO"] == str(mod.REPO)


def test_a_stack_command_runs_in_the_checkout_with_the_checkouts_worker_env(monkeypatch, tmp_path):
    """The project's toolchain lives in `backend/`; `.swarm-env` sits at the worktree root.
    The command must run in the WORKTREE's backend/ with the worktree's env — not the
    project's backend/, and not without the env because it was looked for in backend/."""
    worktree = tmp_path / "wt"
    (worktree / "backend").mkdir(parents=True)
    (worktree / ".swarm-env").write_text("export DB_NAME=proj_w3\n")
    project = tmp_path / "primary"
    (project / "backend").mkdir(parents=True)
    monkeypatch.setattr(cmd, "CHECKOUT", worktree)
    monkeypatch.setattr("models.resolve.REPO", project)

    seen = {}

    def runner(command, **kw):
        seen.update(kw)
        class R:
            returncode = 0
            stdout = "ok"
            stderr = ""
        return R()

    stack = Stack(name="s", root="backend", detect_any=(), detect_language=(), description="", dependency_dir="",
                  bootstrap={}, env={}, commands={"test": "run-the-suite"})
    out = cmd.run_key(stack, "test", runner=runner, check_tool=False)
    assert out.status != cmd.ABSENT
    assert Path(seen["cwd"]) == worktree / "backend"
    assert seen["env"]["DB_NAME"] == "proj_w3"


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv")
def test_run_sh_from_a_worktree_tests_that_worktree(tmp_path):
    """The field repro, end to end: a worker's worktree with the implementation deliberately
    broken, run.sh invoked the way a worker invokes it — MAD_HARNESS_REPO naming the
    primary, no inherited caller directory. It must FAIL."""
    primary = tmp_path / "primary"
    shutil.copytree(PLUGIN / "harness" / "wavelab" / "base", primary)
    (primary / "harness.yaml").write_text(
        "name: C\nslug: c\nstacks: [python-uv]\nareas: []\npaths: {}\n"
        "lanes: {backend: {stacks: [python-uv], agent: fullstack-engineer, cap: 1}}\ntesting: {layout: {}}\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("MAD_HARNESS_")}
    env.pop("VIRTUAL_ENV", None)
    subprocess.run(["uv", "lock", "--quiet"], cwd=primary, check=True, env=env)
    subprocess.run(["uv", "sync", "--quiet"], cwd=primary, check=True, env=env)
    _git(primary, "init", "-q", "-b", "main")
    _git(primary, "add", "-A")
    _git(primary, "commit", "-q", "-m", "base")
    wt = primary / ".claude" / "worktrees" / "w1"
    _git(primary, "worktree", "add", "-q", "-b", "harness-w1-x", str(wt))
    subprocess.run(["uv", "sync", "--quiet"], cwd=wt, check=True, env=env)
    contact = wt / "src" / "wavelab" / "contact.py"
    contact.write_text("def clean_contact(contact):\n    return {'broken': True}\n")

    env["MAD_HARNESS_REPO"] = str(primary)
    proc = subprocess.run(
        [str(HARNESS / "verify" / "run.sh"), "--lane", "backend", "test"],
        cwd=wt, env=env, capture_output=True, text=True, timeout=300,
    )
    assert "[FAIL]" in proc.stdout, f"a broken worktree read as green:\n{proc.stdout}\n{proc.stderr}"


def test_checkout_is_the_project_root_within_the_callers_tree_not_the_git_toplevel(tmp_path, monkeypatch):
    """A project inside a larger repository: the caller's git toplevel is the outer repo,
    and the project root is the nearest harness.yaml above the caller."""
    from models import resolve as mod

    outer = tmp_path / "outer"
    project = outer / "plugins" / "thing"
    (project / "src").mkdir(parents=True)
    (project / "harness.yaml").write_text("name: t\nslug: t\nareas: []\n")
    _git(outer, "init", "-q", "-b", "main")
    monkeypatch.setenv("MAD_HARNESS_CALLER_PWD", str(project / "src"))
    assert mod._find_checkout() == project.resolve()
    # a worktree of the project repo resolves to ITS copy of the project root
    (outer / "README").write_text("x\n")
    _git(outer, "add", "-A")
    _git(outer, "commit", "-q", "-m", "init")
    wt = outer / ".claude" / "worktrees" / "w1"
    _git(outer, "worktree", "add", "-q", "-b", "w1", str(wt))
    monkeypatch.setenv("MAD_HARNESS_CALLER_PWD", str(wt / "plugins" / "thing" / "src"))
    assert mod._find_checkout() == (wt / "plugins" / "thing").resolve()
