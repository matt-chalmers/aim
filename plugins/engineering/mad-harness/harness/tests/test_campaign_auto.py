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


def queue(*epics):
    """What `epic-queue.sh --json` answers: every epic runnable unless it says otherwise."""
    rows = [dict({"id": e} if isinstance(e, str) else e) for e in epics]
    for r in rows:
        r.setdefault("title", "")
        r.setdefault("excluded", None)
        r.setdefault("triage", "READY")
        r.setdefault("ready", 3)
    return json.dumps({"epics": rows, "notes": []})


def plumbing(name, argv):
    """The calls every run makes besides the dispatch: the queue, the lease, the signals,
    autosync. None when `name` is something else."""
    if name == "tk.sh" and argv[1] in ("lease", "autosync"):
        return 0, "ok", ""
    if name == "campaign-signals.sh":
        return 0, "recorded", ""
    return None


def test_every_open_epic_gets_its_own_dispatch_and_the_prompt_names_only_it(tmp_path, capsys):
    epics = [{"id": "E-1", "title": "first"}, {"id": "E-2", "title": "second"}]

    def script(name, argv):
        if name == "preflight.sh":
            return 0, "pre-flight: 5 ok. READY", ""
        if name == "epic-queue.sh":
            return 0, queue(*epics), ""
        if (p := plumbing(name, argv)) is not None:
            return p
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
    assert "§1 (the queue) and §2 (triage) have been done" in prompt and "READY with 3 task(s)" in prompt
    assert "2 epic session(s), 2 ended ok" in capsys.readouterr().out
    # THE LEASE wraps every session, and the signals are recorded with the outcome.
    names = [c[0].split("/")[-1] + " " + " ".join(c[1:3]) for c in run.calls]
    i = names.index("dispatch.sh campaign-orchestrator --prompt-file")
    assert names[i - 1] == "tk.sh lease acquire" and names[i + 1] == "tk.sh lease release"
    sig = [c for c in run.calls if c[0].endswith("campaign-signals.sh")]
    assert [c[1:4] for c in sig] == [["E-1", "--outcome", "closed"], ["E-2", "--outcome", "closed"]] and all("--record" in c for c in sig)


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
        if name == "epic-queue.sh":
            return 0, queue(*epics), ""
        if (p := plumbing(name, argv)) is not None:
            return p
        if name == "dispatch.sh":
            return 1, "You've hit your session limit", ""
        raise AssertionError(name)

    run = _runner(script)
    assert mod.main(["--skip-preflight"], runner=run) == mod.STOP_USAGE
    assert len([c for c in run.calls if c[0].endswith("dispatch.sh")]) == 1
    sig = [c for c in run.calls if c[0].endswith("campaign-signals.sh")]
    assert sig and sig[0][1:4] == ["E-1", "--outcome", "stopped"], "a session that did not close records `stopped`, never `closed`"


def test_a_refused_dispatch_is_a_config_problem_and_stops(capsys):
    def script(name, argv):
        if name == "epic-queue.sh":
            return 0, queue("E-1", "E-2"), ""
        if (p := plumbing(name, argv)) is not None:
            return p
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
        if name == "epic-queue.sh":
            return 0, queue("E-1", "E-2", "E-3"), ""
        if (p := plumbing(name, argv)) is not None:
            return p
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
        if name == "epic-queue.sh":
            return 0, queue("E-1"), ""
        if (p := plumbing(name, argv)) is not None:
            return p
        if name == "dispatch.sh":
            return 1, "limit", ""
        raise AssertionError(name)

    assert mod.main(["--skip-preflight"], runner=_runner(script)) == mod.STOP_USAGE
    assert seen == ["restored"]


def test_an_excluded_epic_is_not_dispatched_and_the_reason_is_said(capsys):
    def script(name, argv):
        if name == "epic-queue.sh":
            return 0, queue({"id": "E-1", "excluded": "leased by matt on other-mac"}, "E-2"), ""
        if (p := plumbing(name, argv)) is not None:
            return p
        if name == "dispatch.sh":
            return 0, "closed", ""
        raise AssertionError(name)

    run = _runner(script)
    assert mod.main(["--skip-preflight"], runner=run) == 0
    assert [c[c.index("--task") + 1] for c in run.calls if c[0].endswith("dispatch.sh")] == ["E-2"]
    assert "E-1 excluded — leased by matt on other-mac" in capsys.readouterr().err


def test_a_lease_that_cannot_be_taken_skips_the_epic_without_a_dispatch(capsys):
    def script(name, argv):
        if name == "epic-queue.sh":
            return 0, queue("E-1"), ""
        if name == "tk.sh" and argv[1:3] == ["lease", "acquire"]:
            return 1, "", "held by someone"
        if (p := plumbing(name, argv)) is not None:
            return p
        raise AssertionError(name)

    run = _runner(script)
    assert mod.main(["--skip-preflight"], runner=run) == 1
    assert not [c for c in run.calls if c[0].endswith("dispatch.sh")]
    assert "lease for E-1 not acquired" in capsys.readouterr().out


def test_the_lease_is_released_in_a_finally_even_when_the_dispatch_raises():
    seen = []

    def script(name, argv):
        if name == "tk.sh" and argv[1] == "lease":
            seen.append(argv[2])
            return 0, "ok", ""
        if name == "dispatch.sh":
            raise subprocess.TimeoutExpired(cmd="dispatch.sh", timeout=1)
        raise AssertionError(name)

    with pytest.raises(subprocess.TimeoutExpired):
        mod.run_epic({"id": "E-1", "title": ""}, runner=_runner(script))
    assert seen == ["acquire", "release"]


def test_outcome_is_read_from_the_digest_and_unknown_is_stopped():
    assert mod.outcome_of("Epic E-1 · outcome: parked · 1 wave") == "parked"
    assert mod.outcome_of("Epic E-1: closed and pushed") == "closed"
    assert mod.outcome_of("") == "stopped" and mod.outcome_of("something else") == "stopped"
