"""The two pre-flight guards `campaign-loop` §0 runs before any worker is dispatched.

Both exist because the previous version of the step did NOTHING and looked like a pass:
the port line scraped the stack card for four-digit numbers and found none, so `lsof`
ran with no arguments; the record-size line named a script that had been renamed, so it
errored on every run. A guard that cannot fail is decoration, so each test here first
shows the guard failing.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from models import check_ports, check_refs
from models.project import Project, ProjectError
from models.resolve import HARNESS

PLUGIN = HARNESS.parent


def _project(raw: dict) -> Project:
    return Project(name="x", slug="x", stacks=(), paths={}, areas=(), security={}, raw=raw)


# --- ports are declared data --------------------------------------------------------


def test_ports_absent_means_none_not_an_error():
    assert _project({}).ports() == {}


def test_ports_are_a_named_map_of_valid_numbers():
    assert _project({"ports": {"web": 3000, "api": "8000"}}).ports() == {"web": 3000, "api": 8000}


@pytest.mark.parametrize(
    "raw",
    [
        {"ports": [3000, 8000]},  # a bare list names nothing
        {"ports": {"web": "three thousand"}},
        {"ports": {"web": 0}},
        {"ports": {"web": 70000}},
    ],
)
def test_a_malformed_ports_block_fails_at_config_time_not_at_preflight(raw):
    with pytest.raises(ProjectError):
        _project(raw).ports()


def test_bound_reports_only_the_ports_something_listens_on():
    probe = {3000: [111, 222], 8000: [], 5432: [333]}.__getitem__
    taken = check_ports.bound({"web": 3000, "api": 8000, "db": 5432}, probe)
    assert taken == {"web": (3000, [111, 222]), "db": (5432, [333])}


def test_the_real_probe_sees_a_real_listener():
    """The injectable probe is only worth trusting if the real one works: bind a port
    in this process and expect our own pid back."""
    import os
    import shutil

    if shutil.which("lsof") is None:
        pytest.skip("lsof not on PATH")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        assert os.getpid() in check_ports.lsof_probe(port)
    assert check_ports.lsof_probe(port) == [], "closed, so nothing listens"


def test_main_says_so_when_nothing_is_declared(monkeypatch, capsys):
    """Silence was the bug. No block must produce a sentence, and exit 0."""
    monkeypatch.setattr(check_ports, "load", lambda: _project({}))
    assert check_ports.main([]) == 0
    assert "none declared" in capsys.readouterr().out


def test_main_is_advisory_by_default_and_blocking_under_strict(monkeypatch, capsys):
    monkeypatch.setattr(check_ports, "load", lambda: _project({"ports": {"web": 3000}}))
    monkeypatch.setattr(check_ports, "lsof_probe", lambda port: [4242])
    assert check_ports.main([]) == 0
    err = capsys.readouterr().err
    assert "BOUND: web=3000" in err and "4242" in err
    assert check_ports.main(["--strict"]) == 1


# --- every script the prose names must exist ------------------------------------------


def _plugin_fixture(tmp_path: Path, skill_text: str) -> Path:
    plugin = tmp_path / "plugin"
    (plugin / "skills" / "x").mkdir(parents=True)
    (plugin / "harness" / "checks").mkdir(parents=True)
    (plugin / "harness" / "checks" / "check-record-size.sh").write_text("#!/bin/sh\n")
    (plugin / "skills" / "x" / "SKILL.md").write_text(skill_text)
    return plugin


def test_a_renamed_script_still_named_by_the_prose_is_reported_with_its_sites(tmp_path):
    """The defect as shipped in 0.9.0: three sites named `check-task-size.sh`."""
    plugin = _plugin_fixture(
        tmp_path,
        "run ${CLAUDE_PLUGIN_ROOT}/harness/checks/check-task-size.sh first\n"
        "and ${CLAUDE_PLUGIN_ROOT}/harness/checks/check-record-size.sh is fine.\n"
        "`$CLAUDE_PLUGIN_ROOT/harness/checks/check-task-size.sh` again, in backticks.\n",
    )
    gone = check_refs.missing(plugin)
    assert list(gone) == ["harness/checks/check-task-size.sh"]
    assert gone["harness/checks/check-task-size.sh"] == ["skills/x/SKILL.md:1", "skills/x/SKILL.md:3"]


def test_trailing_punctuation_is_not_part_of_the_path(tmp_path):
    plugin = _plugin_fixture(
        tmp_path, "see ${CLAUDE_PLUGIN_ROOT}/harness/checks/check-record-size.sh.\n"
    )
    assert check_refs.missing(plugin) == {}


def test_the_shipped_plugin_names_nothing_that_does_not_exist():
    assert check_refs.missing(PLUGIN) == {}, "a script was renamed or removed; fix the prose"
