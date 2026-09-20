"""The tracker-sync tail as one call, and the wave close on top of it.

`/swarm` step 9, `/grind` §10 and `/halt` §4 each carried the same ten shell lines, with
the same "in this order, every time" and "`export` is load-bearing" warnings, and two of
the three said `git pull --rebase` — the form `close_epic.py` had recorded fails on beads.
The tests drive the sequence with an injected runner: the ORDER is what is under test, a
stopped run must list what remains, and an upstream that moved must stop before the push.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from models import close_wave
from models import tracker_sync as mod
from models.steps import FAIL, INFO, OK

BACKEND = json.dumps({"name": "beads", "tracked_export": True, "owned_paths": [".beads/"], "export_path": ".beads/issues.jsonl"})


def key(argv: list[str]) -> str:
    name = Path(argv[0]).name
    return f"{name} {argv[1]}" if name in ("git", "tk.sh") else name


class Runner:
    def __init__(self, **answers):
        self.answers = {
            "tk.sh backend": (0, BACKEND + "\n", ""),
            "tk.sh show": (0, json.dumps([{"id": "T-1", "status": "open", "parent": "E-1"}]), ""),
            "tk.sh close": (0, "", ""),
            "tk.sh export": (0, "", ""),
            "render-epic.sh": (0, "", ""),
            "git add": (0, "", ""),
            "git diff": (1, "", ""),
            "git commit": (0, "[main 1a2b3c4] chore(tracker): close T-1\n 1 file changed\n", ""),
            "git rev-parse": (0, "aaaaaaa\n", ""),
            "git pull": (0, "Already up to date.\n", ""),
            "git rev-list": (0, "0\n", ""),
            "git push": (0, "", "   abc..def  main -> main\n"),
            "git status": (0, "## main...origin/main\n", ""),
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
        if callable(out):
            out = out(argv)
        return subprocess.CompletedProcess(argv, rc, out, err)

    def keys(self):
        return [key(c) for c in self.calls]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    (r / "docs" / "proposed" / "E-1-widgets").mkdir(parents=True)
    (r / "harness.yaml").write_text(
        "name: T\nslug: t\nareas: [{path: src, label: code}]\nbeads: {prefix: E}\npaths: {docs: docs, proposed: docs/proposed}\n"
    )
    monkeypatch.setenv("MAD_HARNESS_REPO", str(r))
    monkeypatch.setattr("models.resolve.REPO", r)
    monkeypatch.setattr("models.project.REPO", r)
    monkeypatch.setattr("models.project.PROJECT_FILE", r / "harness.yaml")
    monkeypatch.setattr(mod, "REPO", r)
    monkeypatch.setattr(close_wave, "REPO", r)
    return r


def project():
    from models.project import load

    return load()


def sync(repo, r, **kw):
    kw.setdefault("message", "chore(tracker): close T-1")
    kw.setdefault("export", ".beads/issues.jsonl")
    return mod.sync(project=project(), runner=r, cwd=str(repo), **kw)


# --- the tail ------------------------------------------------------------------------


def test_the_order_is_export_render_add_commit_pull_push_status_autosync(repo):
    r = Runner()
    results = sync(repo, r, epics=["E-1"], restore_autosync=True)
    assert r.keys() == [
        "tk.sh export", "render-epic.sh", "render-epic.sh", "git add", "git diff", "git commit",
        "git rev-parse", "git pull", "git rev-parse", "git push", "git status", "tk.sh autosync",
    ]
    renders = [c for c in r.calls if key(c) == "render-epic.sh"]
    assert renders[0][1:] == ["E-1", "--check", "--write", "docs/proposed/E-1-widgets/tasks.md"], "drift is read before the write"
    assert renders[1][1:] == ["E-1", "--write", "docs/proposed/E-1-widgets/tasks.md"]
    calls = dict(zip(r.keys(), r.calls))
    assert calls["git add"] == ["git", "add", "--", ".beads/issues.jsonl", "docs/proposed/E-1-widgets/tasks.md"], "the export AND the view, nothing wider"
    assert calls["git pull"] == ["git", "pull", "--rebase", "--autostash"]
    assert calls["tk.sh autosync"][1:] == ["autosync", "on"]
    assert all(x.status in (OK, INFO) for x in results)


def test_a_hand_edited_view_is_said_on_the_line_then_regenerated(repo):
    view = repo / "docs" / "proposed" / "E-1-widgets" / "tasks.md"
    view.write_text("someone edited this\n")
    r = Runner()
    real = r.__call__

    def call(argv, **kw):
        if key(argv) != "render-epic.sh":
            return real(argv, **kw)
        r.calls.append(list(argv))
        rc = 1 if "--check" in argv else 0
        return subprocess.CompletedProcess(argv, rc, "", "DRIFT: content differs\n" if rc else "")

    results = mod.sync(message="m", export=".beads/issues.jsonl", epics=["E-1"], project=project(), runner=call, cwd=str(repo))
    line = next(x for x in results if x.name == "render-epic.sh E-1")
    assert line.status == OK and "had drifted" in line.detail and "regenerated" in line.detail


def test_an_epic_with_no_staging_folder_is_skipped_with_a_line_not_a_failure(repo):
    r = Runner()
    results = sync(repo, r, epics=["E-9"])
    assert "render-epic.sh" not in r.keys()
    line = next(x for x in results if x.name == "render-epic.sh E-9")
    assert line.status == OK and "no staging folder" in line.detail and "nothing to render" in line.detail


def test_an_upstream_that_moved_stops_before_the_push_when_asked_and_lists_the_rest(repo):
    """A wave's commit sits on code the gate proved green; a rebase that pulled in another
    actor's commits puts it on code nobody gated."""
    r = Runner(**{"git rev-parse": (0, lambda argv: "aaaaaaa\n" if not getattr(r, "pulled", False) else "bbbbbbb\n", ""),
                  "git rev-list": (0, "3\n", "")})
    real = r.__call__

    def call(argv, **kw):
        out = real(argv, **kw)
        if key(argv) == "git pull":
            r.pulled = True
        return out

    results = mod.sync(message="m", export=".beads/issues.jsonl", stop_if_upstream_moved=True, restore_autosync=True, project=project(), runner=call, cwd=str(repo))
    stopped = results[-1]
    assert stopped.status == FAIL and "pulled in 3 upstream commit(s)" in stopped.detail and "re-run the wave gate" in stopped.detail
    assert "git push" not in r.keys() and "tk.sh autosync" not in r.keys()
    assert stopped.remaining == ["git push", "git status -sb", f"{mod.TK} autosync on"]


def test_an_upstream_that_moved_is_reported_but_does_not_stop_an_epic_close(repo):
    r = Runner(**{"git rev-parse": (0, lambda argv: "aaaaaaa\n" if len([c for c in r.calls if key(c) == "git rev-parse"]) <= 1 else "bbbbbbb\n", ""),
                  "git rev-list": (0, "2\n", "")})
    results = sync(repo, r, stop_if_upstream_moved=False)
    pull = next(x for x in results if x.name.startswith("git pull"))
    assert pull.status == OK and "2 upstream commit(s) pulled in" in pull.detail
    assert "git push" in r.keys()


def test_no_upstream_tracked_means_nothing_to_count_and_no_stop(repo):
    r = Runner(**{"git rev-parse": (128, "", "fatal: no upstream configured\n")})
    results = sync(repo, r, stop_if_upstream_moved=True)
    assert "git rev-list" not in r.keys() and "git push" in r.keys()
    assert all(x.status != FAIL for x in results)


def test_git_add_names_the_export_the_views_and_the_extra_paths_and_nothing_wider(repo):
    r = Runner()
    sync(repo, r, epics=["E-1"], extra_paths=[".spec-archive/2026-09-19-E-1-widgets"])
    add = r.calls[r.keys().index("git add")]
    assert add == ["git", "add", "--", ".beads/issues.jsonl", "docs/proposed/E-1-widgets/tasks.md", ".spec-archive/2026-09-19-E-1-widgets"]


def test_a_backend_with_no_export_and_no_views_commits_nothing_and_says_so(repo):
    r = Runner()
    results = sync(repo, r, export=None)
    assert "git add" not in r.keys() and "git commit" not in r.keys()
    assert any("nothing to commit" in x.detail for x in results)
    assert "git push" in r.keys()


def test_a_hang_in_pull_is_a_failure_with_the_remaining_steps(repo):
    r = Runner(git_pull=subprocess.TimeoutExpired(cmd="git pull", timeout=300))
    results = sync(repo, r, restore_autosync=True)
    stopped = results[-1]
    assert stopped.status == FAIL and "no output within" in stopped.detail
    assert stopped.remaining[0] == "git pull --rebase --autostash" and stopped.remaining[-1].endswith("autosync on")
    assert "git push" not in r.keys()


def test_not_up_to_date_after_the_push_is_a_failure_not_a_pass(repo):
    r = Runner(**{"git status": (0, "## main...origin/main [ahead 1]\n M src/x.py\n", "")})
    results = sync(repo, r)
    status = results[-1]
    assert status.status == FAIL and "[ahead 1]" in status.detail
    assert "src/x.py" not in status.detail, "a dirty tree is not this step's business; the branch line is"


def test_autosync_is_left_off_by_default_and_said_so(repo):
    """A wave inside a campaign must not re-enable the backend's own export — §5 does."""
    r = Runner()
    results = sync(repo, r)
    assert "tk.sh autosync" not in r.keys()
    assert results[-1].status == INFO and "left off" in results[-1].detail


def test_no_push_skips_the_remote_steps_and_says_how_to_finish(repo):
    r = Runner()
    results = sync(repo, r, push=False, restore_autosync=True)
    assert not {"git pull", "git push", "git status", "git rev-parse"} & set(r.keys())
    assert any("push by hand" in x.detail for x in results)
    assert r.keys()[-1] == "tk.sh autosync"


# --- close-wave on top of it -------------------------------------------------------------


def wave(repo, r, closes, **kw):
    return close_wave.run(closes, project=project(), runner=r, cwd=str(repo), **kw)


def test_closes_run_in_ascending_id_order_then_one_sync(repo):
    r = Runner()
    text, code = wave(repo, r, [("T-2", "second"), ("T-1", "first")], restore_autosync=True)
    assert code == 0 and "SYNCED" in text
    closes = [c for c in r.calls if key(c) == "tk.sh close"]
    assert [c[2] for c in closes] == ["T-1", "T-2"]
    assert r.keys().count("tk.sh export") == 1 and r.keys().count("git push") == 1
    assert r.keys()[0] == "tk.sh backend", "the backend question comes before any close"


def test_the_epics_to_render_are_the_parents_of_the_closed_tasks(repo):
    r = Runner(**{"tk.sh show": (0, lambda argv: json.dumps([{"id": argv[2], "status": "open", "parent": "E-1" if argv[2] != "T-3" else None}]), "")})
    wave(repo, r, [("T-1", "a"), ("T-3", "b")])
    renders = [c for c in r.calls if key(c) == "render-epic.sh" and "--check" not in c]
    assert [c[1] for c in renders] == ["E-1"], "one render per distinct parent; an orphan task renders nothing"


def test_a_failed_close_stops_and_lists_the_rest_including_the_sync(repo):
    r = Runner(**{"tk.sh close": (0, lambda argv: (_ for _ in ()).throw(RuntimeError) if False else "", "")})
    r.answers["tk.sh close"] = (1, "", "no such task\n")
    text, code = wave(repo, r, [("T-1", "a"), ("T-2", "b")], restore_autosync=True)
    assert code == 1 and "STOPPED at `tk.sh close T-1" in text
    rest = text.split("Remaining, by hand")[1]
    assert 'close T-1 --reason "a"' in rest and 'close T-2 --reason "b"' in rest
    assert "tk.sh export" in rest and "git push" in rest and "autosync on" in rest
    assert "tk.sh export" not in r.keys(), "the sync did not start"


def test_check_verifies_ids_and_writes_nothing(repo):
    r = Runner(**{"tk.sh show": (0, lambda argv: json.dumps([{"id": argv[2], "status": "closed" if argv[2] == "T-9" else "open", "parent": "E-1"}]), "")})
    text, code = wave(repo, r, [("T-1", "a"), ("T-9", "b")], check_only=True)
    assert code == 1 and "already closed" in text and "would close: a" in text
    assert set(r.keys()) == {"tk.sh show"}


def test_sync_only_runs_no_close(repo):
    r = Runner()
    text, code = wave(repo, r, [], sync_only=True, epics=["E-1"], message="chore(tracker): sync after wave")
    assert code == 0 and "tk.sh close" not in r.keys()
    assert r.calls[r.keys().index("git commit")][-1] == "chore(tracker): sync after wave"
    assert r.calls[r.keys().index("render-epic.sh")][1] == "E-1"


def test_a_backend_that_cannot_be_asked_stops_before_the_first_close(repo):
    r = Runner(**{"tk.sh backend": (1, "", "unknown backend\n")})
    text, code = wave(repo, r, [("T-1", "a")])
    assert code == 1 and "cannot learn the tracked export path" in text
    assert "tk.sh close" not in r.keys()


def test_parse_closes_requires_a_reason_per_task_or_a_default():
    pairs, why = close_wave.parse_closes(["T-2=done", "T-1"], "default")
    assert pairs == [("T-1", "default"), ("T-2", "done")] and not why
    pairs, why = close_wave.parse_closes(["T-1"], None)
    assert not pairs and "no reason" in why


def test_main_closes_for_real_through_the_injected_subprocess(repo, monkeypatch, capsys):
    r = Runner()
    monkeypatch.setattr("models.steps.subprocess.run", r)
    assert close_wave.main(["T-1=done", "--no-push", "--restore-autosync"]) == 0
    out = capsys.readouterr().out
    assert "SYNCED" in out and "git push" not in r.keys() and "tk.sh autosync" in r.keys()


def test_main_refuses_nothing_to_do(capsys):
    with pytest.raises(SystemExit) as exc:
        close_wave.main([])
    assert exc.value.code == 2 and "--sync-only" in capsys.readouterr().err


def test_the_wrapper_is_executable_and_runs_the_module():
    sh = Path(mod.HARNESS) / "swarm" / "close-wave.sh"
    assert sh.exists() and os.access(sh, os.X_OK), "a wrapper nobody can execute is prose"
    text = sh.read_text()
    assert "MAD_HARNESS_CALLER_PWD" in text and "python -m models.close_wave" in text


def test_the_scripts_the_tail_calls_ship_and_are_executable():
    for p in (mod.TK, mod.RENDER):
        assert p.exists() and os.access(p, os.X_OK), p
