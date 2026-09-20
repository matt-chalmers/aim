"""The verification gate as one call. The tests drive it with an injected runner (the
brief, the suite, the note), a fake worktree and a fake dispatch, because the SEQUENCE
and the rules are what is under test: L3 is handed no diff path, a missing verdict is
never a PASS, a red suite is could-not-judge, and the note is written only on unanimity.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from models import lens_gate as mod
from models.fanout import HUNG, OK, JobResult
from models.resume import VERIFIED


def key(argv):
    name = Path(argv[0]).name
    return f"{name} {argv[1]}" if name in ("git", "tk.sh") else name


class Runner:
    def __init__(self, root: Path, l4_fires=False, **answers):
        info = {
            "brief": str(root / "brief.md"), "root": str(root), "diff_root": str(root.parent / "briefs-diff" / root.name),
            "artefacts": str(root.parent / "briefs-diff" / root.name / "artefacts.md"), "commit": "a" * 40, "files": 3,
            "l4": {"fires": l4_fires, "why": ["security.paths `src/auth/`: src/auth/x.py"] if l4_fires else [], "surface": "none of the declared security invariants", "touched_security_path": l4_fires},
        }
        self.answers = {
            "brief.sh": (0, json.dumps(info) + "\n", ""),
            "run.sh": (0, "[ok] python-uv test  42 passed\n", ""),
            "tk.sh note": (0, "", ""),
        }
        self.answers.update({k.replace("_", " "): v for k, v in answers.items()})
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        a = self.answers[key(argv)]
        if isinstance(a, BaseException):
            raise a
        rc, out, err = a
        return subprocess.CompletedProcess(argv, rc, out, err)

    def keys(self):
        return [key(c) for c in self.calls]


def fake_dispatch(verdicts: dict[str, str | None], statuses: dict[str, str] | None = None):
    """A dispatch that writes each lens's result file with the given verdict (None = no
    VERDICT line) and answers with the given job status."""
    seen = {}

    def go(jobs, cap):
        seen["jobs"] = jobs
        out = []
        for j in jobs:
            path = Path(j.argv[j.argv.index("--out") + 1])
            v = verdicts.get(j.name, "PASS")
            body = "some reasoning\n" if v is None else f"VERDICT: {v}\nFINDINGS:\n- blocking: thing\n" if v == "FAIL" else f"VERDICT: {v}\nAC1: MET — x.py\n"
            path.write_text(body)
            st = (statuses or {}).get(j.name, OK)
            out.append(JobResult(j.name, j.argv, st, 0 if st == OK else (None if st == HUNG else 1), 7, f"full: {path} (3 lines)", "", j.task))
        return out

    go.seen = seen
    return go


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "briefs" / "T-1-aaaaaaaa"
    r.mkdir(parents=True)
    (r / "brief.md").write_text("# brief\n")
    monkeypatch.setattr(mod, "REPO", tmp_path)
    return r


def gate(root, r, dispatch, **kw):
    kw.setdefault("worktree_for", lambda branch, lane, worker, task: root.parent.parent / "wt" / branch)
    return mod.run("T-1", "a" * 40, runner=r, cwd=str(root.parent.parent), dispatch_jobs=dispatch, **kw)


# --- the sequence --------------------------------------------------------------------


def test_brief_suite_prompts_then_four_dispatches_when_l4_fires_three_otherwise(root):
    r = Runner(root, l4_fires=True)
    d = fake_dispatch({})
    text, code, facts = gate(root, r, d, branch="harness-w1-T-1", lane="backend", worker=1)
    assert code == 0, text
    assert r.keys() == ["brief.sh", "run.sh", "tk.sh note"], "brief, then the suite once, then the note — nothing else by the gate itself"
    assert [j.name for j in d.seen["jobs"]] == ["L1", "L2", "L3", "L4"]
    assert facts["l4_fired"] and facts["verified"]

    r = Runner(root, l4_fires=False)
    d = fake_dispatch({})
    text, code, facts = gate(root, r, d)
    assert code == 0 and [j.name for j in d.seen["jobs"]] == ["L1", "L2", "L3"]
    assert not facts["l4_fired"]


def test_l4_always_dispatches_the_security_lens_without_a_trigger(root):
    d = fake_dispatch({})
    gate(root, Runner(root), d, l4="always")
    assert "L4" in [j.name for j in d.seen["jobs"]]


def test_the_suite_runs_in_the_branch_worktree_and_l2_l3_are_dispatched_there(root):
    """The lenses used to run in the primary at main, before step 8 merged the branch —
    L2 re-ran a suite without the change, L3 read a repository without it."""
    r = Runner(root)
    d = fake_dispatch({})
    gate(root, r, d, branch="harness-w2-T-1", lane="backend", worker=2)
    suite = r.calls[r.keys().index("run.sh")]
    assert suite[1:] == ["--lane", "backend", "test"]
    # the runner records cwd via kw; check through the jobs instead
    by = {j.name: j.argv for j in d.seen["jobs"]}
    wt = str(root.parent.parent / "wt" / "harness-w2-T-1")
    assert "--cwd" in by["L2"] and by["L2"][by["L2"].index("--cwd") + 1] == wt
    assert "--cwd" in by["L3"] and by["L3"][by["L3"].index("--cwd") + 1] == wt
    assert "--cwd" not in by["L1"], "L1 reads the diff by sha; it needs no worktree"


def test_without_a_branch_the_change_is_on_the_primary_and_no_worktree_is_made(root):
    made = []
    d = fake_dispatch({})
    gate(root, Runner(root), d, worktree_for=lambda *a: made.append(a) or root)
    assert made == []
    assert all("--cwd" not in j.argv for j in d.seen["jobs"])


def test_a_worktree_that_cannot_be_had_is_could_not_judge(root):
    def boom(*a):
        raise RuntimeError("no such branch")

    r = Runner(root)
    text, code, _ = gate(root, r, fake_dispatch({}), branch="harness-w9-T-1", worktree_for=boom)
    assert code == 2 and "could not judge — no such branch" in text and "run.sh" not in r.keys()


# --- L3's independence ----------------------------------------------------------------


def test_l3_prompt_names_brief_md_and_no_diff_path(root):
    d = fake_dispatch({})
    gate(root, Runner(root), d)
    p = (root / "prompts" / "l3.md").read_text()
    assert str(root / "brief.md") in p
    assert "briefs-diff" not in p and "artefacts" not in p.lower() and "by-file" not in p
    for lens in ("l1", "l2"):
        assert "artefacts.md" in (root / "prompts" / f"{lens}.md").read_text()


# --- a missing verdict is never a PASS ---------------------------------------------------


def test_any_fail_is_exit_1_and_no_verified_note_is_written(root):
    r = Runner(root)
    text, code, facts = gate(root, r, fake_dispatch({"L3": "FAIL"}), branch="harness-w1-T-1")
    assert code == 1 and "VERDICT: FAIL" in text
    assert "tk.sh note" not in r.keys() and not facts["verified"]
    assert "L3 FAIL → fullstack-engineer --resume harness-w1-T-1" in text


def test_a_missing_verdict_or_a_denied_lens_is_exit_2_never_pass(root):
    r = Runner(root)
    text, code, _ = gate(root, r, fake_dispatch({"L2": None}))
    assert code == 2 and "NONE — no `VERDICT:` line" in text and "tk.sh note" not in r.keys()
    assert "COULD NOT JUDGE" in text

    r = Runner(root)
    text, code, _ = gate(root, r, fake_dispatch({}, statuses={"L1": "fail"}))
    assert code == 2 and "dispatch exit 1" in text and "tk.sh note" not in r.keys()

    r = Runner(root)
    text, code, _ = gate(root, r, fake_dispatch({}, statuses={"L3": HUNG}))
    assert code == 2 and "NONE — hung" in text and "tk.sh note" not in r.keys()


def test_a_fail_beside_a_missing_verdict_is_still_a_fail(root):
    """A FAIL is a FAIL; the missing lens is reported, and the task is not verified."""
    r = Runner(root)
    text, code, _ = gate(root, r, fake_dispatch({"L1": "FAIL", "L2": None}))
    assert code == 1 and "tk.sh note" not in r.keys()


def test_a_red_suite_before_the_lenses_is_could_not_judge(root):
    r = Runner(root, **{"run.sh": (1, "[FAIL] python-uv test  2 failed\n", "")})
    text, code, _ = gate(root, r, fake_dispatch({}))
    assert code == 2 and "RED before any lens ran" in text and "believe-the-report" in text
    assert not (root / "prompts").exists(), "no lens was dispatched over a red suite"


def test_a_hung_suite_is_could_not_judge(root):
    r = Runner(root, **{"run.sh": subprocess.TimeoutExpired(cmd="run.sh", timeout=1800)})
    text, code, _ = gate(root, r, fake_dispatch({}))
    assert code == 2 and "no output within" in text


def test_suite_skip_dispatches_without_running_it(root):
    r = Runner(root)
    text, code, _ = gate(root, r, fake_dispatch({}), suite="skip")
    assert code == 0 and "run.sh" not in r.keys() and "skipped (--suite skip)" in text


# --- the note, and its reader --------------------------------------------------------------


def test_all_pass_writes_a_note_resume_point_parses(root):
    r = Runner(root, l4_fires=True)
    text, code, _ = gate(root, r, fake_dispatch({}))
    assert code == 0
    note = r.calls[r.keys().index("tk.sh note")]
    assert note[1:3] == ["note", "T-1"]
    assert note[3] == "VERIFIED aaaaaaaaaaaa: L1 PASS · L2 PASS · L3 PASS · L4 PASS"
    m = VERIFIED.search(note[3])
    assert m and ("a" * 40).startswith(m.group(1)), "the writer's line is what the reader parses"


def test_a_note_that_cannot_be_written_is_said_and_the_gate_is_not_a_pass(root):
    r = Runner(root, **{"tk.sh note": (1, "", "record past the 64KB ceiling\n")})
    text, code, facts = gate(root, r, fake_dispatch({}))
    assert code == 2 and "write it by hand" in text and "VERIFIED aaaaaaaaaaaa" in text and not facts["verified"]


def test_routing_follows_the_table(root):
    d = fake_dispatch({"L2": "FAIL", "L4": "FAIL"})
    text, code, _ = gate(root, Runner(root, l4_fires=True), d, branch="harness-w1-T-1")
    assert code == 1
    assert "L2 FAIL → quality-engineer, after step 8 merges the branch" in text
    assert "L4 FAIL → fullstack-engineer --resume harness-w1-T-1, and raise the task's priority" in text


def test_a_test_shaped_l1_fail_routes_to_the_quality_engineer(root):
    def d(jobs, cap):
        out = []
        for j in jobs:
            path = Path(j.argv[j.argv.index("--out") + 1])
            path.write_text("VERDICT: FAIL\nclassification: test-shaped\n- blocking: decorative assertion\n" if j.name == "L1" else "VERDICT: PASS\n")
            out.append(JobResult(j.name, j.argv, OK, 0, 3, "", "", j.task))
        return out

    text, code, _ = gate(root, Runner(root), d)
    assert code == 1 and "L1 FAIL → quality-engineer" in text


# --- the manifest ---------------------------------------------------------------------------


def test_the_round_is_recorded_on_the_wave_manifest_and_counted(root, tmp_path):
    from models import wave_manifest as wm

    p = wm.open_wave("E-1", lane="backend", planned=["T-1"], dropped=[], wave_base="b", base=tmp_path)
    gate(root, Runner(root), fake_dispatch({"L1": "FAIL"}), wave=str(p))
    gate(root, Runner(root), fake_dispatch({}), wave=str(p))
    rounds = wm.load(p)["lenses"]["T-1"]
    assert [x["round"] for x in rounds] == [1, 2]
    assert rounds[0]["L1"] == "FAIL" and not rounds[0]["verified"]
    assert rounds[1]["L1"] == "PASS" and rounds[1]["verified"]


def test_dry_run_builds_everything_and_dispatches_nothing(root):
    called = []
    text, code, _ = gate(root, Runner(root), lambda jobs, cap: called.append(jobs), dry_run=True)
    assert code == 0 and called == [] and "DRY RUN" in text and (root / "prompts" / "l1.md").exists()


def test_the_wrapper_is_executable_and_runs_the_module():
    sh = Path(mod.HARNESS) / "swarm" / "lens-gate.sh"
    assert sh.exists() and os.access(sh, os.X_OK)
    assert "python -m models.lens_gate" in sh.read_text()


def test_the_scripts_the_gate_calls_ship_and_are_executable():
    for p in (mod.BRIEF, mod.RUN, mod.DISPATCH, mod.TK):
        assert p.exists() and os.access(p, os.X_OK), p


def test_verified_note_and_its_reader_agree():
    from models.resume import verified_note

    line = verified_note("0123456789abcdef0123", {"L1": "PASS", "L2": "PASS", "L3": "PASS"})
    assert line == "VERIFIED 0123456789ab: L1 PASS · L2 PASS · L3 PASS"
    assert VERIFIED.search(line).group(1) == "0123456789ab"
