"""Integration as one call: refs verified before the slot, merges from the ref in order,
a conflict aborted and left unmerged, the slot released in a finally, the gate once."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from models import merge_wave as mod
from models.project import Project, Stack
from models.steps import FAIL


def key(argv):
    name = Path(argv[0]).name
    if name == "git":
        return f"git {argv[1]}" + (f" {argv[2]}" if argv[1] == "merge" and argv[2] == "--abort" else "")
    return f"{name} {argv[1]}" if name == "tk.sh" else name


class Runner:
    def __init__(self, **answers):
        self.answers = {
            "git rev-parse": (0, "abc123def456\n", ""),
            "git status": (0, "", ""),
            "tk.sh backend": (0, '{"name": "beads", "owned_paths": [".beads/"], "export_path": ".beads/issues.jsonl"}\n', ""),
            "tk.sh slot-acquire": (0, "", ""),
            "tk.sh slot-release": (0, "", ""),
            "tk.sh slot-check": (0, '{"free": false, "holder": "w9", "stale": true}\n', ""),
            "tk.sh note": (0, "", ""),
            "git merge": (0, "Merge made by the 'ort' strategy.\n", ""),
            "git merge --abort": (0, "", ""),
            "git diff": (0, "src/x.py\n", ""),
            "git log": (0, "", ""),
            "run.sh": (0, "  [ok  ] python-uv:lint           1.0s  ruff check .\n  [--  ] python-uv:typecheck   stack declares no 'typecheck'\n  [ok  ] python-uv:test          9.0s  pytest\n", ""),
        }
        self.answers.update({k.replace("_", " "): v for k, v in answers.items()})
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        a = self.answers[key(argv)]
        if isinstance(a, BaseException):
            raise a
        rc, out, err = a
        if callable(out):
            out = out(argv)
        return subprocess.CompletedProcess(argv, rc, out, err)

    def keys(self):
        return [key(c) for c in self.calls]


def project():
    stack = Stack(name="python-uv", description="", dependency_dir=".venv", bootstrap={}, env={}, commands={"test": "pytest"})
    return Project(name="T", slug="t", stacks=(stack,), paths={}, areas=(), security={}, raw={})


def go(r, branches, **kw):
    kw.setdefault("stacks", ["python-uv"])
    return mod.run(branches, project=project(), runner=r, cwd="/repo", holder="merge-wave-test", **kw)


def test_branches_merge_ascending_from_the_ref_inside_one_slot():
    r = Runner()
    text, code, facts = go(r, ["harness-w2-T-10", "harness-w1-T-2"])
    assert code == 0 and "GREEN" in text
    merges = [c for c in r.calls if key(c) == "git merge"]
    assert [c[-1] for c in merges] == ["harness-w1-T-2", "harness-w2-T-10"], "ascending by TASK id, numerically — from the REF"
    assert all(c[1:4] == ["merge", "--no-ff", "--no-edit"] for c in merges)
    ks = r.keys()
    assert ks.index("tk.sh slot-acquire") < ks.index("git merge") < ks.index("tk.sh slot-release") < ks.index("run.sh"), "the slot wraps the merges; the gate runs after it is released"
    assert [m["task"] for m in facts["merged"]] == ["T-2", "T-10"]


def test_a_conflict_is_aborted_left_unmerged_and_the_rest_continue():
    """Never resolved here: the two authors are the worst parties to arbitrate."""
    r = Runner(**{"git merge": (0, lambda argv: (_ for _ in ()).throw(KeyError) if False else "ok", "")})

    def merge(argv):
        return "CONFLICT (content): Merge conflict in src/x.py\n" if argv[-1] == "harness-w2-T-2" else "Merge made.\n"

    r.answers["git merge"] = (0, merge, "")
    real = r.__call__

    def call(argv, **kw):
        out = real(argv, **kw)
        if key(argv) == "git merge" and argv[-1] == "harness-w2-T-2":
            out.returncode = 1
        return out

    text, code, facts = mod.run(["harness-w1-T-1", "harness-w2-T-2", "harness-w3-T-3"], project=project(), runner=call, cwd="/repo", holder="h", stacks=["python-uv"])
    assert code == 1 and "NOT LANDED" in text and "1 conflict(s)" in text
    assert facts["conflicts"] == [{"task": "T-2", "branch": "harness-w2-T-2", "paths": ["src/x.py"]}]
    assert [m["task"] for m in facts["merged"]] == ["T-1", "T-3"], "the rest of the wave still merges"
    assert "git merge --abort" in r.keys()
    assert "step-3 planning miss" in text and "re-queue T-2" in text


def test_a_non_ref_argument_refuses_before_acquiring_the_slot():
    r = Runner(**{"git rev-parse": (0, lambda argv: "" if argv[-1] == "refs/heads/harness-w9-typo" else "abc\n", "")})
    real = r.__call__

    def call(argv, **kw):
        out = real(argv, **kw)
        if key(argv) == "git rev-parse" and argv[-1] == "refs/heads/harness-w9-typo":
            out.returncode = 1
        return out

    text, code, _ = mod.run(["harness-w1-T-1", "harness-w9-typo"], project=project(), runner=call, cwd="/repo", holder="h")
    assert code == 2 and "not a branch" in text and "REFUSED before the slot was taken" in text
    assert "tk.sh slot-acquire" not in r.keys() and "git merge" not in r.keys()


def test_a_dirty_tree_refuses_before_the_slot():
    r = Runner(git_status=(0, " M src/y.py\n", ""))
    text, code, _ = go(r, ["harness-w1-T-1"])
    assert code == 2 and "dirty" in text and "tk.sh slot-acquire" not in r.keys()


def test_the_trackers_own_residue_is_not_dirt_but_a_source_change_beside_it_still_is():
    """Measured: pre-flight's `autosync off` rewrites .beads/config.yaml for the run, and
    every wave's merge was refused — the orchestrator committed the flag or set
    skip-worktree by hand. Owned paths are named and ignored; anything else still refuses."""
    r = Runner(git_status=(0, " M .beads/config.yaml\n", ""))
    text, code, _ = go(r, ["harness-w1-T-1"])
    assert code != 2 and "tracker residue ignored: .beads/config.yaml" in text
    assert "tk.sh slot-acquire" in r.keys()
    r = Runner(git_status=(0, " M .beads/config.yaml\n M src/y.py\n", ""))
    text, code, _ = go(r, ["harness-w1-T-1"])
    assert code == 2 and "1 path(s) dirty" in text and "src/y.py" in text
    r = Runner(**{"git status": (0, " M .beads/config.yaml\n", ""), "tk.sh backend": (1, "", "no backend")})
    text, code, _ = go(r, ["harness-w1-T-1"])
    assert code == 2, "a backend that cannot say what it owns leaves the strict rule in force"


def test_a_held_slot_is_reported_with_its_holder_and_nothing_merges():
    r = Runner(**{"tk.sh slot-acquire": (1, "", "held\n")})
    text, code, _ = go(r, ["harness-w1-T-1"])
    assert code == 1 and "held by w9" in text and "STALE" in text and "git merge" not in r.keys()


def test_slot_is_released_even_when_a_merge_raises():
    r = Runner(**{"git merge": RuntimeError("something unexpected")})
    with pytest.raises(RuntimeError):
        mod.merge_all(["harness-w1-T-1"], "h", r, "/repo", False)
    assert r.keys()[-1] == "tk.sh slot-release"


def test_a_git_that_hangs_mid_wave_stops_the_merges_and_is_not_a_conflict():
    r = Runner(**{"git merge": subprocess.TimeoutExpired(cmd="git merge", timeout=120)})
    results, merged, conflicts = mod.merge_all(["harness-w1-T-1", "harness-w2-T-2"], "h", r, "/repo", False)
    assert merged == [] and conflicts == []
    assert [x.status for x in results if x.name.startswith("merge")] == [FAIL]
    assert r.keys().count("git merge") == 1 and r.keys()[-1] == "tk.sh slot-release"


def test_gate_runs_per_stack_and_an_undeclared_key_is_a_dash_line():
    r = Runner()
    text, code, facts = go(r, ["harness-w1-T-1"], stacks=["python-uv", "node"])
    runs = [c for c in r.calls if key(c) == "run.sh"]
    assert {c[2] for c in runs} == {"python-uv", "node"} and all(c[3:] == ["lint", "typecheck", "test"] for c in runs)
    assert facts["gate"]["stacks"]["python-uv"] == {"lint": "ok", "typecheck": "--", "test": "ok"}


def test_a_stack_with_no_declared_key_is_not_green():
    r = Runner(**{"run.sh": (0, "  [--  ] node:lint  declares no\n  [--  ] node:typecheck  declares no\n  [--  ] node:test  declares no\n", "")})
    text, code, facts = go(r, ["harness-w1-T-1"], stacks=["node"])
    assert code == 1 and "nothing measured" in text and facts["gate"]["status"] == "red"


def test_red_is_attributed_by_git_log_and_nothing_is_reverted(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("")
    red = "  [ok  ] python-uv:lint  ruff\n  [FAIL] python-uv:test   12.0s  pytest  exit 1\n         FAILED tests/test_x.py::test_thing - assert 1 == 2\n"
    r = Runner(**{"run.sh": (1, red, ""), "git log": (0, "feat: thing (T-2)\nfix: other (T-1)\n", "")})
    text, code, facts = mod.run(["harness-w1-T-1", "harness-w2-T-2"], project=project(), runner=r, cwd=str(tmp_path), holder="h", stacks=["python-uv"])
    assert code == 1 and facts["gate"]["status"] == "red"
    assert set(facts["gate"]["attributed"]) == {"T-1", "T-2"}
    assert "tests/test_x.py ←" in text and "suggested, not done: `git revert -m 1" in text
    assert "git revert" not in r.keys()
    logged = [c for c in r.calls if key(c) == "git log"][0]
    assert logged[-1] == "tests/test_x.py" and "abc123def456..HEAD" in logged


def test_dry_run_takes_the_slot_merges_nothing_and_releases_it():
    r = Runner()
    text, code, _ = go(r, ["harness-w1-T-1"], dry_run=True)
    assert code == 0 and "git merge" not in r.keys() and "would merge" in text
    assert "tk.sh slot-acquire" in r.keys() and "tk.sh slot-release" in r.keys() and "run.sh" not in r.keys()


def test_merged_conflicts_and_gate_are_recorded_on_the_manifest(tmp_path, monkeypatch):
    from models import wave_manifest as wm

    p = wm.open_wave("E-1", lane="backend", planned=["T-1"], dropped=[], wave_base="b", base=tmp_path)
    r = Runner()
    go(r, ["harness-w1-T-1"], wave=str(p))
    doc = wm.load(p)
    assert doc["merged"][0]["task"] == "T-1" and doc["gate"]["status"] == "green"


def test_task_ids_sort_numerically_within_a_prefix():
    assert sorted(["harness-w1-T-10", "harness-w3-T-9", "harness-w2-T-2"], key=mod.sort_key) == ["harness-w2-T-2", "harness-w3-T-9", "harness-w1-T-10"]


def test_the_wrapper_is_executable_and_runs_the_module():
    sh = Path(mod.__file__).resolve().parent.parent / "swarm" / "merge-wave.sh"
    assert sh.exists() and os.access(sh, os.X_OK) and "python -m models.merge_wave" in sh.read_text()
