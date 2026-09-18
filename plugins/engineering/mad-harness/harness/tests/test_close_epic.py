"""The epic close-out as one call: three gates, then five writes, nothing written until
every gate has passed.

Measured: §5 was eight tool calls per epic against an orchestrator context averaging
~380k tokens. The tests drive the sequence with an injected runner and a tmp_path
project — no tracker, no git remote — because what is under test is the ORDER and the
gate: a failing check must write nothing, `--check` must write nothing, and a write that
fails must say what remains.
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
from pathlib import Path

import pytest

from models import close_epic as mod
from models.project import Project, load

BACKEND = json.dumps({"name": "beads", "tracked_export": True, "owned_paths": [".beads/"], "export_path": ".beads/issues.jsonl"})
REGISTER_CLEAN = "── docs/proposed/E-1-widgets/decisions.md\n   OK — 0 open, 2 settled\n"
REGISTER_OPEN = "── docs/proposed/E-1-widgets/decisions.md\n   OK — 1 open, 2 settled  ← epic cannot fold in or close\n"
NO_REGISTER = "NO REGISTER for E-1 — this epic predates the decision-register flow.\n  The decision gate did NOT run.\n"


def key(argv: list[str]) -> str:
    name = Path(argv[0]).name
    return f"{name} {argv[1]}" if name in ("git", "tk.sh") else name


class Runner:
    """Answers each command from a table; records every call, in order."""

    def __init__(self, **answers):
        self.answers = {
            "check-blocking-prose.sh": (0, "No blocking claim in prose lacks a matching edge.\n", ""),
            "check-decision-register.sh": (0, REGISTER_CLEAN, ""),
            "git log": (0, "", ""),
            "tk.sh backend": (0, BACKEND + "\n", ""),
            "tk.sh close": (0, "", ""),
            "tk.sh export": (0, "", ""),
            "git add": (0, "", ""),
            "git diff": (1, "", ""),  # something staged
            "git commit": (0, "[main 1a2b3c4] chore(tracker): close E-1\n 1 file changed, 3 insertions(+)\n", ""),
            "git pull": (0, "Already up to date.\n", ""),
            "git push": (0, "", "To github.com:o/r.git\n   abc..def  main -> main\n"),
            "tk.sh autosync": (0, "", ""),
        }
        self.answers.update({k.replace("_", " "): v for k, v in answers.items()})
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        answer = self.answers[key(argv)]
        if isinstance(answer, BaseException):
            raise answer
        rc, out, err = answer
        return subprocess.CompletedProcess(argv, rc, out, err)

    def keys(self) -> list[str]:
        return [key(c) for c in self.calls]

    def wrote(self) -> list[str]:
        """Every call that changes something. The gate's whole promise is that this is
        empty when a gate failed."""
        return [k for k in self.keys() if k in WRITES]


WRITES = {"tk.sh close", "tk.sh export", "git add", "git commit", "git pull", "git push", "tk.sh autosync"}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A tmp_path project with staging and an archive declared. Nothing is written into
    the real tree: the config loader and the repo root are pointed here."""
    r = tmp_path / "repo"
    (r / "docs" / "proposed").mkdir(parents=True)
    (r / "harness.yaml").write_text(
        "name: T\nslug: t\nareas: [{path: src, label: code}]\nbeads: {prefix: E}\n"
        "paths: {docs: docs, proposed: docs/proposed, archive: .spec-archive}\n"
    )
    monkeypatch.setenv("MAD_HARNESS_REPO", str(r))
    monkeypatch.setattr("models.resolve.REPO", r)
    monkeypatch.setattr("models.project.REPO", r)
    monkeypatch.setattr("models.project.PROJECT_FILE", r / "harness.yaml")
    monkeypatch.setattr(mod, "REPO", r)
    return r


def project(repo) -> Project:
    return load()


def go(repo, runner, *, check=False, push=True, epic="E-1", reason="shipped the widget; L1-L3 green"):
    return mod.run(epic, reason, project(repo), check=check, push=push, runner=runner, cwd=str(repo))


def stage(repo, *files):
    d = repo / "docs" / "proposed" / "E-1-widgets"
    d.mkdir(parents=True, exist_ok=True)
    for f in files:
        (d / f).write_text("staged\n")
    return d


def archive(repo):
    d = repo / ".spec-archive" / "2026-09-19-E-1-widgets"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("---\nstatus: archived\n---\n")
    return d


# --- the gate ------------------------------------------------------------------------


def test_the_gates_run_first_and_a_failing_check_writes_nothing(repo):
    """The whole point. A gate that failed after `tk.sh close` had run is an epic closed
    over the very thing the gate exists to catch."""
    archive(repo)
    r = Runner(**{"check-blocking-prose.sh": (1, "1 blocking claim(s) stated in prose with NO dependency edge:\n\n  E-1.3  says it is gated on E-1.2\n", "")})
    text, code = go(repo, r)
    assert code == 1
    assert "GATE FAILED — nothing written" in text and "E-1.3  says it is gated" in text
    assert r.wrote() == [], f"a failing gate wrote: {r.wrote()}"
    assert r.calls[0][-1] == "--strict", "advisory mode exits 0 over the very finding §5 forbids"


def test_every_gate_runs_even_after_the_first_fails_so_the_report_is_whole(repo):
    stage(repo, "design.md")
    r = Runner(**{"check-blocking-prose.sh": (1, "1 blocking claim(s)…\n", "")})
    text, _ = go(repo, r)
    assert "check-decision-register.sh" in r.keys()
    assert sum(ln.startswith("[FAIL]") for ln in text.splitlines()) == 2 and "design.md" in text


def test_an_open_decision_fails_the_gate_even_though_the_register_check_exits_zero(repo):
    """The check exits 0 with open rows — that is a CONSISTENT register — and prints a
    marker beside it. `passes with nothing open` is the marker's absence."""
    archive(repo)
    r = Runner(**{"check-decision-register.sh": (0, REGISTER_OPEN, "")})
    text, code = go(repo, r)
    assert code == 1 and "open decision(s)" in text and "1 open, 2 settled" in text
    assert r.wrote() == []


def test_the_open_marker_is_the_one_the_register_check_actually_prints(tmp_path, monkeypatch, capsys):
    """The gate matches a string; this proves the string is the check's own, so the
    two cannot drift into a gate that passes over every open decision."""
    import tracker
    from tracker import check_register
    from tracker.check_register import OPEN_MARKER
    from tracker.port import OPEN, Task

    r = tmp_path / "repo"
    reg = r / "docs" / "proposed" / "E-1-widgets"
    reg.mkdir(parents=True)
    (reg / "decisions.md").write_text("---\nopen:\n  - task: D-1\nsettled: []\n---\n")
    (r / "harness.yaml").write_text("name: T\nslug: t\nareas: []\npaths: {proposed: docs/proposed}\n")
    monkeypatch.setenv("MAD_HARNESS_REPO", str(r))
    monkeypatch.setattr("models.resolve.REPO", r)
    monkeypatch.setattr("models.project.PROJECT_FILE", r / "harness.yaml")

    class Store:
        def show(self, tid):
            return Task(tid, "decision", OPEN, title="which?")

    monkeypatch.setattr(tracker, "task_store", lambda *a, **k: Store())
    assert check_register.main(["E-1"]) == 0, "open rows are consistent — the check passes"
    assert OPEN_MARKER in capsys.readouterr().out
    assert OPEN_MARKER in REGISTER_OPEN, "the fixture the gate tests use carries the real marker"


def test_a_register_the_epic_predates_passes_with_its_warning_on_the_line(repo):
    archive(repo)
    r = Runner(**{"check-decision-register.sh": (0, NO_REGISTER, "")})
    text, code = go(repo, r, check=True)
    assert code == 0 and "decision gate did NOT run" in text


def test_a_surviving_staged_file_fails_the_gate_and_is_named(repo):
    """A staged file that survives its epic is a second source of truth — the failure
    that produced dozens of orphan changelog files."""
    stage(repo, "proposal.md", "tasks.md")
    r = Runner()
    text, code = go(repo, r)
    assert code == 1 and "2 staged file(s) survive" in text
    assert "docs/proposed/E-1-widgets/proposal.md" in text and "archive-epic.sh E-1" in text
    assert r.wrote() == []


def test_an_empty_staging_folder_is_retired_but_still_needs_its_archive_entry(repo):
    stage(repo)  # the folder exists and is empty
    r = Runner(**{"git log": (0, "abc1234\n", "")})
    text, code = go(repo, r, check=True)
    assert code == 1 and "no .spec-archive/*-E-1-* exists" in text and "abc1234" in text
    assert r.wrote() == []


def test_with_an_archive_declared_emptiness_alone_is_not_evidence_of_fold_in(repo):
    """An epic that deleted its folder without folding anything in is just as empty.
    Git is asked whether anything was ever staged: `never` passes, `yes` does not."""
    never = Runner(**{"git log": (0, "", "")})
    text, code = go(repo, never, check=True)
    assert code == 0 and "nothing was ever staged" in text
    logged = [c for c in never.calls if key(c) == "git log"][0]
    assert "--diff-filter=A" in logged and logged[-1].startswith("docs/proposed/")
    assert any(spec == "docs/proposed/E-1*" for spec in logged), "the staging folder's own spelling"

    once = Runner(**{"git log": (0, "deadbee\n", "")})
    text, code = go(repo, once, check=True)
    assert code == 1 and "commit deadbee staged files" in text and "indistinguishable from discarded" in text


def test_an_archive_entry_satisfies_the_gate_under_either_id_form(repo):
    archive(repo)
    text, code = go(repo, Runner(), check=True)
    assert code == 0 and "archived at .spec-archive/2026-09-19-E-1-widgets" in text
    text, code = go(repo, Runner(), check=True, epic="1")
    assert code == 0 and "archived at" in text, "the bare id, expanded through the declared prefix"


def test_without_an_archive_an_absent_or_empty_folder_passes(repo):
    (repo / "harness.yaml").write_text(
        "name: T\nslug: t\nareas: [{path: src, label: code}]\npaths: {docs: docs, proposed: docs/proposed}\n"
    )
    text, code = go(repo, Runner(), check=True)
    assert code == 0 and "deletion is the retirement" in text
    stage(repo)
    text, code = go(repo, Runner(), check=True)
    assert code == 0 and "is empty" in text


def test_no_staging_root_declared_is_a_failed_gate_not_a_clean_one(repo):
    """A wrong guess globs nothing and reports clean — the failure this gate exists to
    catch, turned on itself."""
    (repo / "harness.yaml").write_text("name: T\nslug: t\nareas: []\n")
    text, code = go(repo, Runner(), check=True)
    assert code == 1 and "declares no paths.proposed" in text


# --- --check -------------------------------------------------------------------------


def test_check_mode_runs_the_gates_and_never_writes(repo):
    archive(repo)
    r = Runner()
    text, code = go(repo, r, check=True)
    assert code == 0 and "GATES PASSED — nothing written (--check)" in text
    assert r.wrote() == [] and "tk.sh backend" not in r.keys()


# --- the writes ----------------------------------------------------------------------


def test_a_clean_gate_runs_every_write_in_order_and_commits_the_declared_export(repo):
    archive(repo)
    r = Runner()
    text, code = go(repo, r, reason='shipped; "L1-L3" green')
    assert code == 0 and "CLOSED E-1" in text
    assert r.keys() == [
        "check-blocking-prose.sh", "check-decision-register.sh",
        "tk.sh backend", "tk.sh close", "tk.sh export",
        "git add", "git diff", "git commit", "git pull", "git push", "tk.sh autosync",
    ]
    calls = dict(zip(r.keys(), r.calls))
    assert calls["tk.sh close"][1:] == ["close", "E-1", "--reason", 'shipped; "L1-L3" green']
    assert calls["git add"] == ["git", "add", "--", ".beads/issues.jsonl"], "the path the backend declared, nothing wider"
    assert calls["git commit"][-1] == "chore(tracker): close E-1"
    assert calls["git pull"] == ["git", "pull", "--rebase", "--autostash"]
    assert calls["tk.sh autosync"][1:] == ["autosync", "on"]
    assert "1 file changed" in text


def test_the_export_path_comes_from_the_backend_not_from_a_constant(repo):
    """`owned_paths` is `.beads/` — which also holds the config `autosync off` rewrote.
    Committing that would record `export.auto: false` and dirty the tree again at (h)."""
    archive(repo)
    other = json.dumps({"name": "mdfiles", "tracked_export": True, "owned_paths": [".harness/tasks/", "docs/tasks/"], "export_path": "docs/tasks/"})
    r = Runner(**{"tk.sh backend": (0, other, "")})
    go(repo, r)
    assert ["git", "add", "--", "docs/tasks/"] in r.calls
    assert not any(".beads" in " ".join(c) for c in r.calls)


def test_a_backend_with_no_tracked_export_skips_the_commit_and_says_so(repo):
    archive(repo)
    none = json.dumps({"name": "x", "tracked_export": False, "owned_paths": ["x/"], "export_path": None})
    r = Runner(**{"tk.sh backend": (0, none, "")})
    text, code = go(repo, r)
    assert code == 0 and "nothing to commit" in text
    assert "git add" not in r.keys() and "git commit" not in r.keys()
    assert "git push" in r.keys() and "tk.sh autosync" in r.keys()


def test_nothing_staged_means_no_commit_and_the_report_says_so(repo):
    archive(repo)
    r = Runner(git_diff=(0, "", ""))
    text, code = go(repo, r)
    assert code == 0 and "git commit" not in r.keys()
    assert "no commit made" in text and "git push" in r.keys()


def test_no_push_stops_after_the_commit_and_still_restores_autosync(repo):
    """`--no-push` omits exactly (g). Autosync still comes back on: the tracker writes
    for this epic are done, and nothing else restores what §0 disabled."""
    archive(repo)
    r = Runner()
    text, code = go(repo, r, push=False)
    assert code == 0
    assert "git pull" not in r.keys() and "git push" not in r.keys()
    assert r.keys()[-3:] == ["git diff", "git commit", "tk.sh autosync"]
    assert "push by hand" in text


def test_a_failed_push_stops_there_and_reports_the_remaining_steps_by_hand(repo):
    archive(repo)
    r = Runner(git_push=(1, "", "error: failed to push some refs to 'github.com:o/r.git'\n"))
    text, code = go(repo, r)
    assert code == 1
    assert "STOPPED at `git push`" in text and "failed to push some refs" in text
    tail = text.split("Remaining, by hand")[1]
    assert "git push" in tail and "autosync on" in tail
    assert "tk.sh close" not in tail and "git pull" not in tail, "done steps are not listed as remaining"
    assert "tk.sh autosync" not in r.keys(), "the sequence stopped at the failure"


def test_a_failed_close_reports_everything_after_it_including_the_reason(repo):
    archive(repo)
    r = Runner(**{"tk.sh close": (1, "", "no such task: E-1\n")})
    text, code = go(repo, r, reason="what shipped")
    assert code == 1 and "STOPPED at `tk.sh close" in text and "no such task" in text
    tail = text.split("Remaining, by hand")[1]
    assert 'close E-1 --reason "what shipped"' in tail and "git add .beads/issues.jsonl" in tail
    assert "git pull --rebase --autostash" in tail and "git push" in tail and "autosync on" in tail
    assert "tk.sh export" not in r.keys()


def test_a_hang_in_a_write_is_a_failure_with_the_remaining_steps(repo):
    archive(repo)
    r = Runner(git_pull=subprocess.TimeoutExpired(cmd="git pull", timeout=300))
    text, code = go(repo, r)
    assert code == 1 and "STOPPED at `git pull" in text and "no output within" in text


def test_a_backend_that_cannot_be_asked_stops_before_the_first_write(repo):
    archive(repo)
    r = Runner(**{"tk.sh backend": (1, "", "unknown tracker backend 'x'\n")})
    text, code = go(repo, r)
    assert code == 1 and "cannot learn the tracked export path" in text
    assert r.wrote() == []


# --- the command surface ---------------------------------------------------------------


def test_the_reason_is_required_unless_checking(repo, monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        mod.main(["E-1"])
    assert exc.value.code == 2 and "--reason is required" in capsys.readouterr().err
    archive(repo)
    monkeypatch.setattr("models.steps.subprocess.run", Runner())
    assert mod.main(["E-1", "--check"]) == 0
    assert "nothing written (--check)" in capsys.readouterr().out


def test_main_closes_for_real_through_the_injected_subprocess(repo, monkeypatch, capsys):
    archive(repo)
    r = Runner()
    monkeypatch.setattr("models.steps.subprocess.run", r)
    assert mod.main(["E-1", "--reason", "done", "--no-push"]) == 0
    assert "CLOSED E-1" in capsys.readouterr().out and "git push" not in r.keys()


def test_the_wrapper_is_executable_and_runs_the_module():
    sh = Path(mod.HARNESS) / "swarm" / "close-epic.sh"
    assert sh.exists() and os.access(sh, os.X_OK)
    text = sh.read_text()
    assert "MAD_HARNESS_CALLER_PWD" in text and "python -m models.close_epic" in text


def test_the_scripts_the_gates_call_ship_and_are_executable():
    for name in ("check-blocking-prose.sh", "check-decision-register.sh", "archive-epic.sh"):
        p = mod.CHECKS / name
        assert p.exists() and os.access(p, os.X_OK), name
    assert mod.TK.exists() and os.access(mod.TK, os.X_OK)


# --- the capability the commit step relies on --------------------------------------------


def test_beads_declares_the_file_its_export_writes_by_default():
    """`export_path` is what `git add` names. It must be the very path `export()` writes
    with no argument, or the close-out commits a file the tracker did not regenerate."""
    from tracker.beads import TRACKED_EXPORT, BeadsTaskStore

    caps = BeadsTaskStore(cwd="/nowhere").capabilities()
    assert caps.export_path == TRACKED_EXPORT == inspect.signature(BeadsTaskStore.export).parameters["path"].default
    assert caps.export_path.startswith(caps.owned_paths[0]), "the export lives under what the backend owns"
    assert caps.export_path != caps.owned_paths[0], "narrower than the prefix, which also holds config.yaml"


def test_mdfiles_declares_its_configured_export_dir_or_none(tmp_path, monkeypatch):
    from tracker.mdfiles import MdTaskStore

    repo = tmp_path / "repo"
    (repo / "docs" / "tasks").mkdir(parents=True)
    monkeypatch.setenv("MAD_HARNESS_REPO", str(repo))
    store = MdTaskStore(root=repo / ".harness" / "tasks", export_dir=repo / "docs" / "tasks")
    assert store.capabilities().export_path == "docs/tasks/"
    assert MdTaskStore(root=repo / ".harness" / "tasks").capabilities().export_path is None
