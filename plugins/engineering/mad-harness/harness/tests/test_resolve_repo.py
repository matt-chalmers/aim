"""Which repository the harness operates on — and that getting it wrong is LOUD.

Installed as a plugin, the harness lives in `~/.claude/plugins/cache/…`, nowhere near
the project. Every wrapper `cd`s into the harness before Python starts, and the harness
carries its own `harness.yaml`, so a resolver that walks up from the process's cwd finds
the harness and returns it — a valid-looking project with no records. The tracker then
returned an empty backlog with exit 0, and an unattended campaign reported a clean,
zero-work run against 17 open epics.

`conftest.py` sets `MAD_HARNESS_REPO`, which is exactly how the suite stayed green under
the wrong resolution. These tests remove it and drive the real wrappers from a foreign
directory — the case the suite never saw.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from models.resolve import HARNESS, RepoError, _find_repo

PLUGIN = HARNESS.parent
#: The harness's own project name, read from its config rather than typed: the wrong
#: answer these tests guard against is "the harness answered about ITSELF".
OWN_NAME = str(yaml.safe_load((PLUGIN / "harness.yaml").read_text())["name"])
MINIMAL_CONFIG = """\
name: Consumer
slug: consumer
stacks: []
areas: []
paths: {}
testing:
  layout: {}
"""


@pytest.fixture
def no_override(monkeypatch):
    monkeypatch.delenv("MAD_HARNESS_REPO", raising=False)
    monkeypatch.delenv("MAD_HARNESS_CALLER_PWD", raising=False)


@pytest.fixture
def consumer(tmp_path) -> Path:
    """A consuming repository: a git checkout with a harness.yaml at its root."""
    repo = tmp_path / "consumer"
    repo.mkdir()
    (repo / "harness.yaml").write_text(MINIMAL_CONFIG)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


def _wrapper_env(caller: Path | None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("MAD_HARNESS_REPO", "MAD_HARNESS_CALLER_PWD")}
    return env


# --- the resolver ---------------------------------------------------------------


def test_the_callers_directory_wins_over_the_harness_own_config(no_override, consumer, monkeypatch):
    """The process cwd IS the harness (that is what every wrapper's `cd` produces);
    the caller's directory says otherwise, and the caller is right."""
    monkeypatch.chdir(HARNESS)
    monkeypatch.setenv("MAD_HARNESS_CALLER_PWD", str(consumer / "src"))
    (consumer / "src").mkdir()
    assert _find_repo() == consumer.resolve()


def test_a_repo_without_a_config_yet_resolves_to_its_git_root(no_override, tmp_path, monkeypatch):
    """`/harness-setup` runs before a harness.yaml exists. The git fallback must run
    FROM THE CALLER'S DIRECTORY: run from the process cwd it answered about the
    harness's own checkout."""
    repo = tmp_path / "fresh"
    (repo / "deep").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    monkeypatch.chdir(HARNESS)
    monkeypatch.setenv("MAD_HARNESS_CALLER_PWD", str(repo / "deep"))
    assert _find_repo() == repo.resolve()


def test_a_caller_outside_any_project_is_refused_not_answered_with_the_harness(
    no_override, tmp_path, monkeypatch
):
    """The last resort used to be `HARNESS.parent` — the plugin cache, which carries a
    harness.yaml and no records. Confidently wrong is the one answer this must not give."""
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()
    monkeypatch.chdir(HARNESS)
    monkeypatch.setenv("MAD_HARNESS_CALLER_PWD", str(nowhere))
    with pytest.raises(RepoError) as exc:
        _find_repo()
    assert str(nowhere) in str(exc.value)
    assert "MAD_HARNESS_REPO" in str(exc.value)


def test_the_explicit_override_still_wins(no_override, consumer, tmp_path, monkeypatch):
    monkeypatch.setenv("MAD_HARNESS_REPO", str(consumer))
    monkeypatch.setenv("MAD_HARNESS_CALLER_PWD", str(tmp_path))
    assert _find_repo() == consumer.resolve()


# --- the wrappers, end to end -------------------------------------------------------


def test_a_wrapper_run_from_a_consuming_repo_answers_about_that_repo(no_override, consumer):
    """The regression the bug report asked for: a real wrapper, no MAD_HARNESS_REPO, a
    foreign cwd. It must name the consuming project — not the harness itself."""
    proc = subprocess.run(
        [str(PLUGIN / "harness" / "checks" / "check-project-config.sh")],
        cwd=consumer,
        env=_wrapper_env(consumer),
        capture_output=True,
        text=True,
        timeout=120,
    )
    first = proc.stdout.splitlines()[0] if proc.stdout else proc.stderr
    assert first == "project: Consumer (consumer)", (proc.stdout, proc.stderr)
    assert OWN_NAME not in proc.stdout


def test_a_wrapper_run_from_nowhere_fails_and_names_the_directory(no_override, tmp_path):
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()
    proc = subprocess.run(
        [str(PLUGIN / "harness" / "checks" / "check-project-config.sh")],
        cwd=nowhere,
        env=_wrapper_env(nowhere),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode != 0
    assert str(nowhere) in proc.stderr, proc.stderr
    assert OWN_NAME not in proc.stdout


def test_every_wrapper_records_the_caller_before_moving():
    """The fix is only as good as its coverage: a wrapper that `cd`s into the harness
    without recording the caller reintroduces the bug for that one script."""
    offenders = []
    for sh in sorted((PLUGIN / "harness").rglob("*.sh")):
        if "wavelab" in sh.parts or ".venv" in sh.parts:
            continue
        text = sh.read_text()
        if 'cd "$(dirname "$0")/..' in text and "MAD_HARNESS_CALLER_PWD" not in text:
            offenders.append(str(sh.relative_to(PLUGIN)))
    assert offenders == [], f"wrappers that cd without recording the caller: {offenders}"
