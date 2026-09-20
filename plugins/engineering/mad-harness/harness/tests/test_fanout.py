"""N commands at once, bounded, each with a timeout, every one answered — and the
detach/wait shape that replaces the headless orchestrator's hand-written sleep loop."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from models import fanout as mod
from models import wave_manifest as wm
from models.fanout import HUNG, OK, Job, parse_jobs, run_jobs

PY = sys.executable


def job(name, *code, timeout=30, task=None, cwd="/tmp"):
    return Job(name=name, argv=(PY, "-c", " ".join(code)), cwd=cwd, timeout=timeout, task=task)


def test_jobs_run_concurrently_under_the_cap_and_every_job_answers():
    jobs = [job(f"j{i}", "import time; time.sleep(0.6); print('done')") for i in range(4)]
    started = time.monotonic()
    results = run_jobs(jobs, cap=4)
    elapsed = time.monotonic() - started
    assert [r.name for r in results] == ["j0", "j1", "j2", "j3"], "results come back in the jobs' order"
    assert all(r.status == OK and r.rc == 0 and r.first_line == "done" for r in results)
    assert elapsed < 2.0, f"four 0.6s jobs took {elapsed:.1f}s — they ran serially"


def test_the_cap_bounds_concurrency():
    jobs = [job(f"j{i}", "import time; time.sleep(0.5)") for i in range(4)]
    started = time.monotonic()
    run_jobs(jobs, cap=1)
    assert time.monotonic() - started >= 1.9, "cap=1 must serialise"


def test_a_hung_job_is_killed_and_reported_HUNG_not_ok():
    """The recorded 8.5-hour stall was a command blocking on a prompt nobody saw."""
    jobs = [job("stuck", "import time; time.sleep(30)", timeout=1), job("fine", "print('ok')")]
    results = run_jobs(jobs, cap=2)
    stuck, fine = results
    assert stuck.status == HUNG and stuck.rc is None and "killed after" in stuck.line()
    assert fine.status == OK


def test_a_failing_job_is_fail_with_its_exit_code():
    r = run_jobs([job("bad", "import sys; print('boom'); sys.exit(3)")], cap=1)[0]
    assert r.status == "fail" and r.rc == 3 and r.first_line == "boom"


def test_the_full_path_is_read_from_the_digest_line():
    r = run_jobs([job("d", "print('T-1 · PASS · 3 files'); print('... full: /x/y/out.md (42 lines)')")], cap=1)[0]
    assert r.first_line == "T-1 · PASS · 3 files" and r.full_path == "/x/y/out.md"


def test_a_shell_shaped_line_is_refused():
    jobs, problems = parse_jobs("echo a && echo b\necho 'a && b'\necho x | cat\necho $(date)\n# a comment\necho fine --task T-9\n", "/tmp", 10)
    assert [j.argv for j in jobs] == [("echo", "a && b"), ("echo", "fine", "--task", "T-9")], "a quoted operator is data; a bare one is shell"
    assert len(problems) == 3 and all("no shell" in p for p in problems)
    assert jobs[1].task == "T-9" and jobs[1].name.endswith(":T-9")


def test_detach_then_wait_returns_5_while_running_and_0_when_done(tmp_path):
    jobs = [job("slow", "import time; time.sleep(2); print('slow done')"), job("quick", "print('quick done')")]
    run_id = mod.detach(jobs, cap=2, base=tmp_path)
    assert (tmp_path / "fanout" / run_id / "jobs.json").exists()
    done, results, all_jobs = mod.wait(run_id, timeout=1, base=tmp_path, poll=0.2)
    text, code = mod.report(results, all_jobs, done)
    assert not done and code == mod.EXIT_RUNNING and "still running" in text
    done, results, all_jobs = mod.wait(run_id, timeout=15, base=tmp_path, poll=0.2)
    text, code = mod.report(results, all_jobs, done)
    assert done and code == 0, text
    assert {r.name: r.first_line for r in results} == {"slow": "slow done", "quick": "quick done"}
    assert (tmp_path / "fanout" / run_id / "job-0.rc").read_text().strip() == "0"


def test_dispatched_is_recorded_on_the_manifest(tmp_path):
    path = wm.open_wave("E-1", lane="backend", planned=["T-1"], dropped=[], wave_base="abc", base=tmp_path)
    r = run_jobs([Job("1:T-1", (PY, "-c", "print('T-1 · PASS · done'); print('full: /o/T-1.md (3 lines)')", "fullstack-engineer", "--task", "T-1"), "/tmp", 30, "T-1")], cap=1)
    mod.record_dispatched(str(path), r)
    doc = wm.load(path)
    assert doc["dispatched"]["T-1"] == {"agent": "fullstack-engineer", "exit": 0, "status": "ok", "first_line": "T-1 · PASS · done", "full": "/o/T-1.md"}


def test_main_refuses_a_bad_jobs_file_and_starts_nothing(tmp_path, capsys):
    f = tmp_path / "jobs.txt"
    f.write_text("echo a; echo b\n")
    assert mod.main(["--jobs", str(f)]) == 2
    assert "nothing started" in capsys.readouterr().err


def test_main_runs_a_jobs_file_and_reports(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("MAD_HARNESS_CALLER_PWD", str(tmp_path))
    f = tmp_path / "jobs.txt"
    f.write_text(f"{PY} -c 'print(1)'\n{PY} -c 'import sys; sys.exit(1)'\n")
    assert mod.main(["--jobs", str(f), "--cap", "2"]) == 1
    out = capsys.readouterr().out
    assert "[ok  ]" in out and "[FAIL]" in out and "1 ok, 1 not ok" in out


def test_the_wrapper_is_executable_and_runs_the_module():
    sh = Path(mod.__file__).resolve().parent.parent / "swarm" / "fanout.sh"
    assert sh.exists() and os.access(sh, os.X_OK)
    assert "python -m models.fanout" in sh.read_text()


# --- the manifest ---------------------------------------------------------------------


def test_open_numbers_the_next_wave_and_current_finds_the_open_one(tmp_path):
    p1 = wm.open_wave("E-1", lane="backend", planned=["T-1"], dropped=[], wave_base="a", base=tmp_path)
    assert p1.name == "E-1-w1.json" and wm.current("E-1", tmp_path) == p1
    wm.close(p1, head="b")
    assert wm.current("E-1", tmp_path) is None
    p2 = wm.open_wave("E-1", lane="backend", planned=["T-2"], dropped=[{"task": "T-3", "reason": "shares x"}], wave_base="b", base=tmp_path)
    assert p2.name == "E-1-w2.json" and wm.current("E-1", tmp_path) == p2
    assert [d["wave"] for d in wm.all_waves("E-1", tmp_path)] == [1, 2]
    assert wm.path_for("E-1-w2", tmp_path) == p2


def test_concurrent_appends_lose_nothing(tmp_path):
    """N lens gates for one wave run at once and each appends its round."""
    import threading

    p = wm.open_wave("E-1", lane="backend", planned=[], dropped=[], wave_base="a", base=tmp_path)

    def add(i):
        for k in range(10):
            wm.append(p, "lenses", {"round": k, "L1": "PASS"}, task=f"T-{i}")

    threads = [threading.Thread(target=add, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    doc = wm.load(p)
    assert all(len(doc["lenses"][f"T-{i}"]) == 10 for i in range(5))


def test_a_malformed_manifest_is_an_error_not_an_empty_wave(tmp_path):
    d = tmp_path / "waves"
    d.mkdir()
    (d / "E-1-w1.json").write_text("{not json")
    with pytest.raises(ValueError, match="malformed"):
        wm.all_waves("E-1", tmp_path)
    with pytest.raises(ValueError):
        wm.load(d / "E-1-w1.json")


def test_the_summary_line_counts_what_a_compaction_must_not_lose(tmp_path):
    p = wm.open_wave("E-1", lane="backend", planned=["T-1", "T-2"], dropped=[], wave_base="a", base=tmp_path)
    wm.append(p, "dispatched", {"agent": "fullstack-engineer"}, task="T-1")
    wm.append(p, "lenses", {"round": 1, "verified": True}, task="T-1")
    wm.append(p, "merged", {"task": "T-1"})
    assert wm.summary_line(wm.load(p)) == "wave E-1-w1 open: 2 planned, 1 dispatched, 1 verified, 1 merged, 0 closed"
