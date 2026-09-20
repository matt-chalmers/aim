"""Wave composition as one call: the mechanical cut is code, the two judgement checks
are printed for the orchestrator with their inputs side by side."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from models import wave_plan as mod
from models.project import Project
from models.resume import ResumePoint


@dataclass
class T:
    id: str
    title: str = ""
    priority: int | None = 2
    created_at: str = "2026-09-01"
    description: str = ""
    acceptance: str = ""
    notes: str = ""
    labels: tuple = ("backend",)


def project(cap=6, megafile=1000):
    return Project(name="T", slug="t", stacks=(), paths={}, areas=(), security={}, raw={"lanes": {"backend": {"cap": cap}}, "signals": {"megafile_lines": megafile}})


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "src").mkdir(parents=True)
    (r / "src" / "a.py").write_text("x = 1\n")
    (r / "src" / "b.py").write_text("y = 2\n")
    (r / "src" / "big.py").write_text("\n" * 1500)
    subprocess.run(["git", "init", "-q", "."], cwd=r, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"], cwd=r, check=True)
    return r


def compose(repo, tasks, n=None, states=None, cap=6, megafile=1000, env=None, lane="backend", parent="E-1"):
    states = states or {}

    def resume(tid):
        st, br = states.get(tid, ("FRESH", None))
        return ResumePoint(task=tid, state=st, branch=br)

    return mod.compose(lane, n, parent=parent, project=project(cap, megafile), cwd=repo, ready=lambda p: tasks, resume_for=resume, env=env or {})


def test_top_n_by_priority_clamped_to_the_lane_cap_and_labels(repo):
    tasks = [T("T-3", priority=3), T("T-1", priority=1), T("T-2", priority=1, created_at="2026-08-01"), T("F-1", labels=("frontend",))]
    plan = compose(repo, tasks, n=10, cap=2)
    assert [r["id"] for r in plan["wave"]] == ["T-2", "T-1"], "P1 before P3; older first among equals; the frontend task is not in this lane"
    assert plan["width"] == 2 and any("lane cap=2" in n for n in plan["notes"])
    assert [d["task"] for d in plan["dropped"]] == ["T-3"] and "beyond width" in plan["dropped"][0]["reason"]


def test_the_global_cap_bounds_the_width_too(repo):
    plan = compose(repo, [T("T-1"), T("T-2"), T("T-3")], cap=6, env={"CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "1"})
    assert plan["width"] == 1 and len(plan["wave"]) == 1


def test_labels_unused_is_stated_and_every_ready_task_is_considered(repo):
    plan = compose(repo, [T("T-1", labels=()), T("T-2", labels=())])
    assert len(plan["wave"]) == 2 and any("labels unused" in n for n in plan["notes"])


def test_a_shared_existing_path_drops_the_lower_priority_and_says_which(repo):
    tasks = [T("T-1", priority=1, description="edit src/a.py"), T("T-2", priority=2, description="also touches src/a.py and src/b.py")]
    plan = compose(repo, tasks)
    assert [r["id"] for r in plan["wave"]] == ["T-1"]
    assert plan["dropped"] == [{"task": "T-2", "reason": "shares src/a.py with T-1"}]


def test_a_megafile_is_width_one(repo):
    tasks = [T("T-1", priority=1, description="a function in src/big.py"), T("T-2", priority=2, description="another function in src/big.py, disjoint")]
    plan = compose(repo, tasks)
    assert [r["id"] for r in plan["wave"]] == ["T-1"]
    assert "megafile src/big.py (1500 lines > 1000) already owned by T-1" in plan["dropped"][0]["reason"]


def test_no_megafile_threshold_is_said_not_assumed(repo):
    plan = compose(repo, [T("T-1", description="src/big.py"), T("T-2", description="src/big.py")], megafile=None)
    # Both name the same EXISTING path, so the shared-path rule drops one anyway — as a shared path, not a megafile.
    assert any("megafile_lines not declared" in n for n in plan["notes"])
    assert plan["dropped"][0]["reason"] == "shares src/big.py with T-1"


def test_would_create_paths_are_printed_for_judgement_not_decided(repo):
    tasks = [T("T-1", description="creates tests/factories/widget.py"), T("T-2", description="creates tests/factories/widget.py too")]
    plan = compose(repo, tasks)
    assert [r["id"] for r in plan["wave"]] == ["T-1", "T-2"], "a path that does not exist yet is not a drop — it is the new-file judgement's input"
    assert plan["wave"][0]["would_create"] == ["tests/factories/widget.py"]
    text = mod.render(plan)
    assert "JUDGEMENT — new files" in text and "creates: tests/factories/widget.py" in text
    assert "JUDGEMENT — shared vocabulary" in text


def test_merge_tree_conflict_drops_and_names_paths(repo):
    def branch(name, path, content):
        subprocess.run(["git", "checkout", "-q", "-b", name, "main"], cwd=repo, check=True)
        (repo / path).write_text(content)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-am", f"{name} change"], cwd=repo, check=True)
        subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)

    subprocess.run(["git", "branch", "-q", "-M", "main"], cwd=repo, check=True)
    branch("harness-w1-T-1", "src/a.py", "x = 'one'\n")
    branch("harness-w2-T-2", "src/a.py", "x = 'two'\n")
    states = {"T-1": ("VERIFY", "harness-w1-T-1"), "T-2": ("VERIFY", "harness-w2-T-2")}
    plan = compose(repo, [T("T-1", priority=1), T("T-2", priority=2)], states=states)
    assert [r["id"] for r in plan["wave"]] == ["T-1"]
    assert "conflicts with harness-w1-T-1: src/a.py" in plan["dropped"][0]["reason"]


def test_a_merge_verdict_needs_no_worker_and_is_listed_separately(repo):
    plan = compose(repo, [T("T-1"), T("T-2")], states={"T-1": ("MERGE", "harness-w1-T-1")})
    assert [r["id"] for r in plan["merge_only"]] == ["T-1"] and [r["id"] for r in plan["wave"]] == ["T-2"]
    assert "MERGE, no worker" in mod.render(plan)


def test_an_undeclared_lane_is_refused_and_nothing_ready_says_why(repo):
    assert "not declared" in compose(repo, [T("T-1")], lane="ops")["error"]
    plan = compose(repo, [])
    assert plan["why_empty"] == "nothing ready under E-1" and "nothing ready" in mod.render(plan)


def test_merge_tree_unavailable_is_said_not_assumed_clean(repo, monkeypatch):
    monkeypatch.setattr(mod, "merge_tree_conflicts", lambda cwd, a, b: (None, []))
    states = {"T-1": ("VERIFY", "b1"), "T-2": ("VERIFY", "b2")}
    plan = compose(repo, [T("T-1"), T("T-2")], states=states)
    assert len(plan["wave"]) == 2 and any("NOT checked" in n for n in plan["notes"])


def test_paths_in_finds_paths_and_ignores_noise(repo):
    ex, new = mod.paths_in("edit src/a.py and see https://x.y/z; ids like PROJ-12 and 3/4 are not paths; add tests/new_test.py", repo)
    assert ex == ["src/a.py"] and new == ["tests/new_test.py"]


def test_the_wrapper_is_executable_and_runs_the_module():
    sh = Path(mod.__file__).resolve().parent.parent / "swarm" / "wave-plan.sh"
    assert sh.exists() and os.access(sh, os.X_OK) and "python -m models.wave_plan" in sh.read_text()
