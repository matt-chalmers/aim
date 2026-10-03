"""The config check's discovery report: it warns, it never gates, and every warning is
answerable — by a `declined:` entry or a /harness-setup revisit."""

from __future__ import annotations

import io
import subprocess
import sys

import pytest
import yaml

import models.check_project as check_project
import models.project as project
from models.preflight import SURFACE
from models.setup_ledger import record, snapshot


@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setattr(project, "REPO", tmp_path)
    monkeypatch.setattr(project, "PROJECT_STACKS_DIR", tmp_path / ".harness" / "stacks")
    monkeypatch.setattr(
        project, "PROJECT_FRAMEWORKS_DIR", tmp_path / ".harness" / "frameworks"
    )
    monkeypatch.setattr(check_project, "plugin_version", lambda: "0.12.0")
    return tmp_path


def _run(repo, monkeypatch, config: dict, *args: str) -> tuple[int, str, str]:
    path = repo / "harness.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "name": "fx",
                "slug": "fx",
                "areas": [],
                "harness": {"version": "0.12.0"},
                "ports": {},
                **config,
            }
        )
    )
    proj = project.load(path)
    monkeypatch.setattr(check_project, "load", lambda: proj)
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    rc = check_project.main(list(args))
    return rc, out.getvalue(), err.getvalue()


def _touch(repo, *rels):
    for rel in rels:
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("")


def test_an_undeclared_present_toolchain_warns_and_names_what_to_run(repo, monkeypatch):
    _touch(repo, "web/package-lock.json")
    rc, out, err = _run(repo, monkeypatch, {})
    assert rc == 0, "a warning, never a failure"
    line = "node-npm is present at web/ (web/package-lock.json) but not declared — /harness-setup revisit"
    assert line in err
    assert "modules:" in out and "node-npm" in out


def test_the_warning_reaches_a_preflight(repo, monkeypatch):
    """The pre-flight surfaces stderr lines matching SURFACE; a warning it drops is one
    the orchestrator never sees."""
    _touch(repo, "web/package-lock.json")
    _, _, err = _run(repo, monkeypatch, {})
    surfaced = [ln for ln in err.splitlines() if SURFACE.match(ln)]
    assert any("node-npm is present at web/" in ln for ln in surfaced)


def test_strict_does_not_promote_a_discovery_warning(repo, monkeypatch):
    _touch(repo, "web/package-lock.json")
    rc, _, err = _run(repo, monkeypatch, {}, "--strict")
    assert "not declared" in err and rc == 0


def test_a_declined_toolchain_neither_warns_nor_fails_and_is_printed(repo, monkeypatch):
    _touch(repo, "web/package-lock.json")
    rc, out, err = _run(
        repo, monkeypatch, {"declined": {"stacks": {"node-npm@web": "a docs site"}}}
    )
    assert rc == 0 and "not declared" not in err
    assert "declined:" in out and "node-npm@web" in out and "a docs site" in out


def test_declining_one_root_does_not_silence_another(repo, monkeypatch):
    _touch(repo, "web/package-lock.json", "tools/package-lock.json")
    _, _, err = _run(
        repo, monkeypatch, {"declined": {"stacks": {"node-npm@tools": "vendored"}}}
    )
    assert "present at web/" in err and "present at tools/" not in err


def test_a_malformed_declined_block_fails_the_check(repo, monkeypatch):
    rc, _, err = _run(repo, monkeypatch, {"declined": {"stacks": {"node-npm@web": ""}}})
    assert rc == 1 and "has no reason" in err


def test_no_ledger_says_nothing(repo, monkeypatch):
    _, _, err = _run(repo, monkeypatch, {})
    assert (
        "since it was confirmed" not in err
        and "since frameworks were confirmed" not in err
    )


def test_a_confirmed_block_edited_by_hand_warns(repo, monkeypatch):
    base = {
        "name": "fx",
        "slug": "fx",
        "areas": [],
        "security": {"paths": ["src/auth"]},
    }
    record("security", "confirmed", base)
    _, _, err = _run(repo, monkeypatch, {"security": {"paths": ["src/api"]}})
    assert (
        "security changed since it was confirmed (your edit, unconfirmed) — /harness-setup revisit"
        in err
    )


def test_a_new_dependency_since_the_frameworks_snapshot_warns_and_never_gates(
    repo, monkeypatch
):
    (repo / "package.json").write_text('{"dependencies": {"express": "4"}}')
    record(
        "frameworks",
        "confirmed",
        {"frameworks": []},
        dependencies=snapshot({".": {"package.json": ["express"]}}),
    )
    (repo / "package.json").write_text(
        '{"dependencies": {"express": "4", "next": "14"}}'
    )
    rc, _, err = _run(repo, monkeypatch, {}, "--strict")
    assert rc == 0
    assert "new dependencies at the root since frameworks were confirmed: next" in err


def test_an_unreadable_ledger_is_one_warning(repo, monkeypatch):
    (repo / ".harness").mkdir()
    (repo / ".harness" / "setup.json").write_text("{nope")
    rc, _, err = _run(repo, monkeypatch, {})
    assert rc == 0 and "setup ledger cannot be read" in err
