"""The outer loop: one fresh orchestrator session per epic, stopping on the things a
script can see — a failed pre-flight, a refused dispatch, a closed usage window."""

from __future__ import annotations

import json
import subprocess

import pytest

from models import campaign_auto as mod


def _runner(script):
    """A runner that answers by the command's first token(s)."""
    calls = []

    def run(argv, cwd=None, capture_output=True, text=True, timeout=None):
        calls.append(argv)
        name = argv[0].split("/")[-1]
        rc, out, err = script(name, argv)
        return subprocess.CompletedProcess(argv, rc, out, err)

    run.calls = calls
    return run


@pytest.fixture(autouse=True)
def _tmp_run_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "RUN_DIR", tmp_path / "campaign")
    monkeypatch.setattr(mod, "last_terminal", lambda cwd=None: "success")


def test_every_open_epic_gets_its_own_dispatch_and_the_prompt_names_only_it(tmp_path, capsys):
    epics = [{"id": "E-1", "title": "first"}, {"id": "E-2", "title": "second"}]

    def script(name, argv):
        if name == "preflight.sh":
            return 0, "pre-flight: 5 ok. READY", ""
        if name == "tk.sh" and argv[1:3] == ["autosync", "on"]:
            return 0, "ok", ""
        if name == "tk.sh":
            return 0, json.dumps(epics), ""
        if name == "dispatch.sh":
            return 0, f"outcome: closed {argv[argv.index('--task') + 1]}\nfull: /x", ""
        raise AssertionError(name)

    run = _runner(script)
    assert mod.main([], runner=run) == 0
    dispatches = [c for c in run.calls if c[0].endswith("dispatch.sh")]
    assert [c[c.index("--task") + 1] for c in dispatches] == ["E-1", "E-2"]
    assert all(c[1] == "campaign-orchestrator" and "--digest" in c and "--out" in c for c in dispatches)
    prompt = (tmp_path / "campaign" / "E-2-prompt.md").read_text()
    assert "`E-2`" in prompt and "E-1" not in prompt and "MODE=auto" in prompt
    assert "2 epic session(s), 2 ended ok" in capsys.readouterr().out


def test_a_failed_preflight_starts_nothing_and_an_upgrade_exit_propagates(capsys):
    def script(name, argv):
        if name == "preflight.sh":
            return 3, "BLOCKED — run /harness-setup", ""
        raise AssertionError(f"{name} must not run after a failed pre-flight")

    run = _runner(script)
    assert mod.main([], runner=run) == 3
    assert not [c for c in run.calls if c[0].endswith("dispatch.sh")]


def test_a_closed_usage_window_stops_the_loop_with_the_rigs_exit_code(monkeypatch, capsys):
    epics = [{"id": "E-1", "title": ""}, {"id": "E-2", "title": ""}]
    monkeypatch.setattr(mod, "last_terminal", lambda cwd=None: "usage_limit")

    def script(name, argv):
        if name == "tk.sh":
            return 0, json.dumps(epics) if "list" in argv else "ok", ""
        if name == "dispatch.sh":
            return 1, "You've hit your session limit", ""
        raise AssertionError(name)

    run = _runner(script)
    assert mod.main(["--skip-preflight"], runner=run) == mod.STOP_USAGE
    assert len([c for c in run.calls if c[0].endswith("dispatch.sh")]) == 1


def test_a_refused_dispatch_is_a_config_problem_and_stops(capsys):
    def script(name, argv):
        if name == "tk.sh":
            return 0, json.dumps([{"id": "E-1"}, {"id": "E-2"}]) if "list" in argv else "ok", ""
        if name == "dispatch.sh":
            return mod.EXIT_REFUSED, "", "FAIL: tiers.verifier-spec: no such tier"
        raise AssertionError(name)

    run = _runner(script)
    assert mod.main(["--skip-preflight"], runner=run) == 1
    assert len([c for c in run.calls if c[0].endswith("dispatch.sh")]) == 1
    assert "no such tier" in capsys.readouterr().err


def test_max_epics_and_epic_narrow_the_queue():
    seen = []

    def script(name, argv):
        if name == "tk.sh":
            return 0, json.dumps([{"id": "E-1"}, {"id": "E-2"}, {"id": "E-3"}]) if "list" in argv else "ok", ""
        if name == "dispatch.sh":
            seen.append(argv[argv.index("--task") + 1])
            return 0, "ok", ""
        raise AssertionError(name)

    assert mod.main(["--skip-preflight", "--max-epics", "2"], runner=_runner(script)) == 0
    assert seen == ["E-1", "E-2"]
    seen.clear()
    assert mod.main(["--skip-preflight", "--epic", "E-3"], runner=_runner(script)) == 0
    assert seen == ["E-3"]


def test_the_last_terminal_is_read_from_the_orchestrators_event_not_a_workers(tmp_path, monkeypatch):
    monkeypatch.undo()  # the autouse fixture stubs last_terminal; this test wants the real one
    ev = tmp_path / ".harness" / "run" / "events"
    ev.mkdir(parents=True)
    rows = [
        {"payload": {"agent": "campaign-orchestrator", "terminal": "usage_limit"}},
        {"payload": {"agent": "fullstack-engineer", "terminal": "success"}},
    ]
    (ev / "harness.dispatch.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert mod.last_terminal(tmp_path) == "usage_limit"
    assert mod.last_terminal(tmp_path / "nowhere") == ""


def test_autosync_is_restored_however_the_loop_ends(monkeypatch):
    """The first headless epic stopped at its ceiling before §5 and left export.auto off."""
    monkeypatch.setattr(mod, "last_terminal", lambda cwd=None: "usage_limit")
    seen = []

    def script(name, argv):
        if name == "tk.sh" and argv[1:3] == ["autosync", "on"]:
            seen.append("restored")
            return 0, "ok", ""
        if name == "tk.sh":
            return 0, json.dumps([{"id": "E-1"}]), ""
        if name == "dispatch.sh":
            return 1, "limit", ""
        raise AssertionError(name)

    assert mod.main(["--skip-preflight"], runner=_runner(script)) == mod.STOP_USAGE
    assert seen == ["restored"]
