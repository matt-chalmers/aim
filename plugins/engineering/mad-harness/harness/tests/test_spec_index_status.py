"""`spec-index-status.sh` answers campaign-loop §3a — REUSE / DELTA / REBUILD — mechanically.

It answered "PyYAML required" for every epic instead: the script resolved config under the
harness venv, then exec'd its body under the SYSTEM python3, which has no PyYAML. A loud
failure, but one `2>/dev/null` from a silent one, and the documented path to the reuse
decision was simply unavailable. These drive the real script from a consuming fixture and
assert the VERDICT, not exit 0.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from models.resolve import HARNESS

SCRIPT = HARNESS / "checks" / "spec-index-status.sh"
CONFIG = """\
name: Consumer
slug: consumer
stacks: []
areas: []
paths:
  proposed: docs/proposed
testing:
  layout: {}
"""


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout.strip()


@pytest.fixture
def consumer(tmp_path) -> Path:
    repo = tmp_path / "consumer"
    (repo / "docs" / "proposed" / "E-1-thing").mkdir(parents=True)
    (repo / "harness.yaml").write_text(CONFIG)
    (repo / "docs" / "cited.md").write_text("v1\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "baseline")
    sha = _git(repo, "rev-parse", "HEAD")
    (repo / "docs" / "proposed" / "E-1-thing" / "spec-index.md").write_text(
        f"---\ngenerated_sha: {sha}\ncites:\n  - docs/cited.md\nverdict: ADEQUATE\n---\n# index\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "index")
    return repo


def _run(repo: Path, epic: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("MAD_HARNESS_REPO", "MAD_HARNESS_CALLER_PWD")}
    # AN OPERATOR'S PATH, NOT THE TEST RUNNER'S. Under `uv run pytest` the harness venv is
    # first on PATH, so a bare `python3` IS the venv interpreter and has PyYAML — which is
    # exactly how the original bug passed every in-tree check. Drop the venv entries.
    env["PATH"] = os.pathsep.join(
        d for d in env.get("PATH", "").split(os.pathsep) if "/.venv/" not in d and "VIRTUAL" not in d
    )
    env.pop("VIRTUAL_ENV", None)
    return subprocess.run(
        [str(SCRIPT), epic], cwd=repo, env=env, capture_output=True, text=True, timeout=120
    )


def test_reuse_when_nothing_cited_has_moved(consumer):
    proc = _run(consumer, "E-1")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "PyYAML" not in proc.stderr and "PyYAML" not in proc.stdout
    assert "REUSE" in proc.stdout, proc.stdout


def test_delta_when_a_cited_doc_changed_since_the_baseline(consumer):
    (consumer / "docs" / "cited.md").write_text("v2\n")
    _git(consumer, "commit", "-qam", "cited doc moved")
    proc = _run(consumer, "E-1")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "DELTA" in proc.stdout and "docs/cited.md" in proc.stdout, proc.stdout


def test_rebuild_when_there_is_no_index(consumer):
    proc = _run(consumer, "E-9")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "REBUILD" in proc.stdout, proc.stdout


# --- BUG 9: either id form; a miss names what it tried --------------------------------------


def test_the_prefixed_id_finds_a_bare_named_folder(consumer):
    """The shape on disk: `<bare>-<slug>`. The id every other command takes: prefixed.
    This used to print a legitimate-looking REBUILD and cost a ~120k-token survey."""
    (consumer / "docs" / "proposed" / "E-1-thing").rename(consumer / "docs" / "proposed" / "1-thing")
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-qm", "bare-named folder")
    proc = _run(consumer, "E-1")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "REUSE" in proc.stdout and "REBUILD" not in proc.stdout, proc.stdout


def test_no_folder_says_so_and_names_the_patterns_tried(consumer):
    proc = _run(consumer, "E-9")
    assert "no staging folder for E-9" in proc.stdout and "Tried:" in proc.stdout, proc.stdout
    assert "E-9*" in proc.stdout and "9*" in proc.stdout, "both id forms were looked for"


def test_a_folder_without_an_index_is_a_real_rebuild(consumer):
    (consumer / "docs" / "proposed" / "E-7-bare").mkdir()
    proc = _run(consumer, "E-7")
    assert "REBUILD" in proc.stdout and "holds no spec-index.md" in proc.stdout, proc.stdout


# --- BUG 10: the DELTA path closes its own loop -----------------------------------------------


def test_stamp_moves_the_baseline_so_a_verified_delta_becomes_reuse(consumer):
    (consumer / "docs" / "cited.md").write_text("v2\n")
    (consumer / "docs" / "new.md").write_text("found by the survey\n")
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-qm", "cited doc moved; a new doc the survey will cite")
    assert "DELTA" in _run(consumer, "E-1").stdout
    stamped = _run_args(consumer, ["E-1", "--stamp", "--cite", "docs/new.md"])
    assert stamped.returncode == 0, (stamped.stdout, stamped.stderr)
    head = _git(consumer, "rev-parse", "--short=12", "HEAD")
    text = (consumer / "docs" / "proposed" / "E-1-thing" / "spec-index.md").read_text()
    assert f"generated_sha: {head}" in text and "generated_at:" in text and "docs/new.md" in text
    assert text.rstrip().endswith("# index"), "the body is untouched"
    assert "REUSE" in _run(consumer, "E-1").stdout, "the same DELTA does not re-fire"


def _run_args(repo: Path, args: list[str]) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("MAD_HARNESS_REPO", "MAD_HARNESS_CALLER_PWD")}
    env["PATH"] = os.pathsep.join(d for d in env.get("PATH", "").split(os.pathsep) if "/.venv/" not in d)
    env.pop("VIRTUAL_ENV", None)
    return subprocess.run([str(SCRIPT), *args], cwd=repo, env=env, capture_output=True, text=True, timeout=120)
