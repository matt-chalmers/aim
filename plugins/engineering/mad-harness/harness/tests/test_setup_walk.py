"""The non-interactive half of /harness-setup, end to end, on real repository shapes.

Everything except the conversation is code, so it can be run without one: derive every
block, render the first-run config, validate it, probe the stack commands, and stand up a
real worker worktree. This is what lets the untestable part — the questions — stay small.
The fixtures are copies of the wave lab's seeded base, so the toolchain is real.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from models.resolve import PLUGIN_ROOT

BASE = PLUGIN_ROOT / "harness" / "wavelab" / "base"
SETUP = PLUGIN_ROOT / "harness" / "setup"
CHECKS = PLUGIN_ROOT / "harness" / "checks"

pytestmark = pytest.mark.skipif(
    shutil.which("uv") is None, reason="the walk needs uv, as the lab does"
)


def _env(repo: Path) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("TRACKER_ACTOR", "SWARM_LANE", "HARNESS_ROOT", "VIRTUAL_ENV")
    }
    env.update(MAD_HARNESS_REPO=str(repo), MAD_HARNESS_CALLER_PWD=str(repo))
    return env


def _run(repo: Path, *argv: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(a) for a in argv],
        cwd=repo,
        env=_env(repo),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _fixture(tmp_path: Path, shape: str) -> Path:
    repo = tmp_path / shape
    shutil.copytree(BASE, repo)
    if shape == "web":
        (repo / "web").mkdir()
        (repo / "web" / "package.json").write_text(
            json.dumps({"name": "web", "private": True})
        )
        (repo / "web" / "package-lock.json").write_text(
            json.dumps({"name": "web", "lockfileVersion": 3, "packages": {}})
        )
    if shape == "unsupported":
        (repo / "go.mod").write_text("module example.com/x\n\ngo 1.22\n")
        (repo / "go.sum").write_text("")
    subprocess.run(
        ["uv", "lock", "--quiet"], cwd=repo, check=True, env=_env(repo), timeout=300
    )
    subprocess.run(["git", "init", "-q", "."], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"],
        cwd=repo,
        check=True,
    )
    return repo


@pytest.mark.parametrize("shape", ["root", "web", "unsupported"])
def test_the_code_half_of_setup_produces_a_working_config(tmp_path, shape):
    repo = _fixture(tmp_path, shape)

    derived = _run(repo, SETUP / "derive.sh")
    assert derived.returncode == 0, derived.stderr
    drafts = {d["block"]: d for d in json.loads(derived.stdout)}

    rendered = _run(repo, SETUP / "write.sh", "--render-first-run")
    assert rendered.returncode == 0, rendered.stdout + rendered.stderr
    raw = yaml.safe_load((repo / "harness.yaml").read_text())
    assert not (repo / ".harness" / "setup.json").exists(), (
        "a first run records no answer"
    )

    check = _run(repo, CHECKS / "check-project-config.sh")
    assert check.returncode == 0, f"FAIL, not OK/WARN:\n{check.stdout}\n{check.stderr}"
    assert "FAIL" not in check.stderr

    stacks = raw["stacks"]
    if shape == "unsupported":
        # Nothing invented for the toolchain no module covers: it is the agent's to name,
        # from the listing the derivation hands over.
        assert stacks == ["python-uv"]
        assert "go.mod" in drafts["stacks"]["evidence"]["listing"]["."]
        assert any("no module covers" in o for o in drafts["stacks"]["owed"])
    if shape == "web":
        assert {"name": "node-npm", "root": "web"} in stacks
        return  # the node toolchain's own commands are not this test's to prove

    commands = _run(repo, CHECKS / "check-stack-commands.sh")
    assert commands.returncode == 0, commands.stdout + commands.stderr

    lane = next(iter(raw["lanes"]))
    probe = _run(repo, PLUGIN_ROOT / "harness" / "swarm" / "probe-worktree.sh", lane)
    assert probe.returncode == 0, probe.stdout + probe.stderr


def test_derive_only_leaves_owed_blocks_absent_and_warns_honestly(tmp_path):
    repo = _fixture(tmp_path, "root")
    out = _run(repo, SETUP / "write.sh", "--derive-only")
    assert out.returncode == 0, out.stdout + out.stderr
    raw = yaml.safe_load((repo / "harness.yaml").read_text())
    assert "security" not in raw and "ports" not in raw
    ledger = json.loads((repo / ".harness" / "setup.json").read_text())
    assert {e["state"] for e in ledger["blocks"].values()} == {"defaulted"}
    check = _run(repo, CHECKS / "check-project-config.sh")
    assert check.returncode == 0 and "WARN" in check.stdout + check.stderr
