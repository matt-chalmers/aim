"""The four wave signals and the circuit breakers, as arithmetic over the wave manifests."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from models import breakers
from models import wave_manifest as wm
from models import wave_report as mod
from models.project import Project


def project(megafile=100, baseline=None):
    return Project(name="T", slug="t", stacks=(), paths={}, areas=(), security={}, raw={"signals": {"megafile_lines": megafile, "baselines": {"escape_rate": baseline}}})


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True, capture_output=True)


def scratch(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", ".")
    (r / "big.py").write_text("\n" * 120)
    (r / "small.py").write_text("x\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "feat: base (T-0)")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=r, capture_output=True, text=True).stdout.strip()
    git(r, "checkout", "-q", "-b", "harness-w1-T-1")
    (r / "big.py").write_text("\n" * 130)
    (r / "small.py").write_text("x\ny\n")
    git(r, "commit", "-q", "-am", "feat: grow (T-1)")
    git(r, "checkout", "-q", "-")
    git(r, "merge", "-q", "--no-ff", "--no-edit", "harness-w1-T-1")
    git(r, "commit", "-q", "--allow-empty", "-m", "fix: something (T-1)")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=r, capture_output=True, text=True).stdout.strip()
    return r, base, head


def manifest(tmp_path, base, head, **over):
    p = wm.open_wave("E-1", lane="backend", planned=["T-1", "T-2"], dropped=[], wave_base=base, base=tmp_path)
    wm.append(p, "dispatched", {"agent": "fullstack-engineer", "exit": 0}, task="T-1")
    wm.append(p, "dispatched", {"agent": "fullstack-engineer", "exit": 0}, task="T-2")
    wm.append(p, "lenses", {"round": 1, "L1": "PASS", "L2": "PASS", "L3": "PASS", "L4": None, "l4_fired": False, "touched_security_path": False, "verified": True}, task="T-1")
    wm.append(p, "lenses", {"round": 1, "L1": "FAIL", "L2": "PASS", "L3": "PASS", "L4": None, "l4_fired": False, "touched_security_path": True, "verified": False}, task="T-2")
    wm.append(p, "lenses", {"round": 2, "L1": "PASS", "L2": "PASS", "L3": "PASS", "L4": "PASS", "l4_fired": True, "touched_security_path": True, "verified": True}, task="T-2")
    wm.append(p, "merged", {"task": "T-1", "branch": "harness-w1-T-1", "merge_sha": head})
    wm.append(p, "conflicts", {"task": "T-2", "branch": "harness-w2-T-2", "paths": ["small.py"]})
    wm.set_key(p, "gate", {"status": "green", "stacks": {}, "attributed": []})
    wm.append(p, "closed", "T-1")
    wm.close(p, head=head)
    for k, v in over.items():
        wm.set_key(p, k, v)
    return p


def test_the_six_signals_from_a_manifest_and_a_scratch_repo(tmp_path):
    repo, base, head = scratch(tmp_path)
    p = manifest(tmp_path, base, head)
    sig = mod.compute(wm.load(p), repo, project(), lambda t: "- AC1\n- AC2\n" if t == "T-1" else "")
    assert sig["first_pass"] == {"passed": 1, "judged": 2, "rate": 0.5}, "T-2's round 1 failed L1; round 2 does not count as first pass"
    assert sig["l4"] == {"fired": 1, "touched": 2, "rate": 0.5}, "T-2 touched a security path twice; L4 fired once"
    assert sig["megafiles_grew"] == [{"path": "big.py", "before": 120, "after": 130}]
    assert sig["escape"]["count"] == 1 and sig["escape"]["of"] == 4
    assert sig["lines_per_criterion"] == [{"task": "T-1", "lines": 11, "criteria": 2, "per": 5.5}]
    assert sig["yield"] == {"closed": 1, "of": 2, "rate": 0.5, "dispatched_unknown": False}
    assert sig["conflicts"] == 1
    text = mod.render(sig, None)
    assert "50% (1/2)" in text and "big.py 120→130" in text and "T-1 11/2" in text and "first wave" in text
    assert "no baseline recorded; this is the first measurement" in text


def test_dispatched_absent_falls_back_visibly(tmp_path):
    repo, base, head = scratch(tmp_path)
    p = manifest(tmp_path, base, head, dispatched={})
    sig = mod.compute(wm.load(p), repo, project(), lambda t: "")
    assert sig["yield"]["dispatched_unknown"] and sig["yield"]["of"] == 2
    assert "`dispatched` not recorded" in mod.render(sig, None)


def test_a_baseline_is_compared_and_direction_is_against_the_previous_wave(tmp_path):
    repo, base, head = scratch(tmp_path)
    p = manifest(tmp_path, base, head)
    sig = mod.compute(wm.load(p), repo, project(baseline=0.1), lambda t: "")
    prev = dict(sig, first_pass={"rate": 1.0}, yield_={"rate": 0.9})
    prev["yield"] = {"rate": 0.9}
    text = mod.render(sig, prev)
    assert "vs baseline 10%" in text and "50% (1/2) ↓" in text


def test_no_criteria_is_flagged_not_divided_by_zero(tmp_path):
    repo, base, head = scratch(tmp_path)
    p = manifest(tmp_path, base, head)
    sig = mod.compute(wm.load(p), repo, project(), lambda t: "")
    assert sig["lines_per_criterion"][0]["per"] is None and "(no criteria!)" in mod.render(sig, None)


def test_megafile_threshold_absent_is_said(tmp_path):
    repo, base, head = scratch(tmp_path)
    p = manifest(tmp_path, base, head)
    sig = mod.compute(wm.load(p), repo, project(megafile=None), lambda t: "")
    assert sig["megafiles_grew"] == [] and "not declared — not measured" in mod.render(sig, None)


# --- breakers ------------------------------------------------------------------------------


def wave(n, *, gate="green", lenses=None, closed=("T-1",), closed_at="x"):
    return {"epic": "E", "wave": n, "gate": {"status": gate, "attributed": ["T-9"] if gate == "red" else []}, "lenses": lenses or {}, "closed": list(closed), "closed_at": closed_at, "opened_at": "2026-09-01"}


def ev(waves, **kw):
    kw.setdefault("ready_now", 3)
    kw.setdefault("open_decisions", [])
    kw.setdefault("recent_outcomes", [])
    return breakers.evaluate(waves, **kw)


def test_nothing_tripped_on_a_healthy_wave():
    assert ev([wave(1)]) == []


def test_gate_red_twice_in_a_row_trips_and_names_the_culprit():
    trips = ev([wave(1, gate="red"), wave(2, gate="red")])
    assert [t["breaker"] for t in trips] == ["wave gate red twice in a row"] and "T-9" in trips[0]["action"]
    assert ev([wave(1, gate="red"), wave(2)]) == [], "not consecutive"


def test_a_task_that_failed_twice_is_gated_and_a_third_round_is_split():
    two = {"T-4": [{"L1": "FAIL"}, {"L1": "FAIL"}]}
    trips = ev([wave(1, lenses=two)])
    assert trips[0]["breaker"] == "T-4 FAILed the lenses twice" and "gate T-4" in trips[0]["action"]
    three = {"T-4": [{"L1": "FAIL"}, {"L2": "FAIL"}, {"L1": "PASS", "L2": "PASS", "L3": "PASS"}]}
    trips = ev([wave(1, lenses={"T-4": three["T-4"][:2]}), wave(2, lenses={"T-4": three["T-4"][2:]})])
    assert trips[0]["breaker"] == "T-4 entered a THIRD lens round" and "SPLIT" in trips[0]["action"]


def test_a_decision_task_is_the_hard_line():
    trips = ev([wave(1)], open_decisions=["D-1"])
    assert "decision" in trips[0]["breaker"] and "tk.sh park" in trips[0]["action"]


def test_max_waves_is_a_hard_park():
    trips = ev([wave(i) for i in range(1, 7)])
    assert any(t["level"] == breakers.EXIT_PARK and "MAX_WAVES" in t["breaker"] for t in trips)
    text, code = breakers.render("E", [wave(i) for i in range(1, 7)], trips)
    assert code == 2 and "[PARK]" in text


def test_zero_closed_gates_what_blocked_and_parks_only_when_nothing_is_ready():
    trips = ev([wave(1, closed=())], ready_now=2)
    assert trips[0]["level"] == breakers.EXIT_TRIPPED and "2 task(s) still ready" in trips[0]["action"]
    trips = ev([wave(1, closed=())], ready_now=0)
    assert trips[0]["level"] == breakers.EXIT_PARK


def test_three_parked_epics_is_stop_the_run():
    trips = ev([wave(1)], recent_outcomes=["parked", "parked", "parked"])
    text, code = breakers.render("E", [wave(1)], trips)
    assert code == 3 and "STOP" in text
    assert ev([wave(1)], recent_outcomes=["closed", "parked", "parked"]) == []


def test_the_wrappers_are_executable_and_run_the_modules():
    root = Path(mod.__file__).resolve().parent.parent / "swarm"
    for n, m in (("wave-report.sh", "wave_report"), ("breakers.sh", "breakers")):
        sh = root / n
        assert sh.exists() and os.access(sh, os.X_OK) and f"python -m models.{m}" in sh.read_text(), n
