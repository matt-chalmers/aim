"""Prove the mutexes hold across real processes, not just across objects in one.

WHY SUBPROCESSES AND NOT THREADS. The thing being guarded is eight `claude -p` workers,
each its own OS process, each invoking the CLI. A threaded test would exercise a lock the
real system never takes, and would pass while the real one raced. These launch the actual
CLI the actual way a worker does.

WHY IT MATTERS EVEN THOUGH CONTENTION IS RARE. The orchestrator assigns a specific id and
forbids a worker from picking another, so in normal operation there is exactly one
contender. The claim exists to catch the abnormal case — a double-dispatch, or a stale
re-dispatch after `/halt`. That case is rare, silent, and expensive: two workers in one
task produce two commits that collide at the merge slot with no earlier symptom. A guard
for a rare failure has to be exactly right, because nothing else will catch it.
"""

from __future__ import annotations

import concurrent.futures
import os
import subprocess
import sys

import pytest

from tracker.locks import RUN_DIR_ENV

WORKERS = 8


def _cli(run_dir, *args: str) -> int:
    """Run the tracker CLI as a worker does: a fresh process, its own interpreter."""
    env = {**os.environ, RUN_DIR_ENV: str(run_dir)}
    return subprocess.run(
        [sys.executable, "-m", "tracker.cli", *args],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env=env,
        capture_output=True,
        text=True,
    ).returncode


def _race(run_dir, make_args) -> list[int]:
    """Fire N processes at once and collect their exit codes."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        # Threads only to LAUNCH concurrently; every contender is a separate process.
        return list(pool.map(lambda n: _cli(run_dir, *make_args(n)), range(WORKERS)))


def test_exactly_one_process_wins_a_contested_claim(tmp_path):
    """The double-dispatch guard. Two winners is two workers in one task."""
    codes = _race(tmp_path / "run", lambda n: ("claim", "T-1", "--actor", f"w{n}"))
    assert codes.count(0) == 1, (
        f"{codes.count(0)} of {WORKERS} processes claimed the same task; "
        f"exactly one must win"
    )
    assert codes.count(1) == WORKERS - 1, "every loser must be told it lost"


def test_exactly_one_process_holds_the_merge_slot(tmp_path):
    """A wave with two holders lands two commits at once — the conflict the slot exists
    to make impossible."""
    codes = _race(tmp_path / "run", lambda n: ("slot-acquire", "--holder", f"w{n}"))
    assert codes.count(0) == 1, f"{codes.count(0)} processes took the merge slot at once"


def test_distinct_tasks_do_not_contend(tmp_path):
    """The mutex must not serialise a whole wave. Eight workers on eight tasks all win."""
    codes = _race(tmp_path / "run", lambda n: ("claim", f"T-{n}", "--actor", f"w{n}"))
    assert codes.count(0) == WORKERS


def test_one_actor_reclaiming_its_own_task_never_loses(tmp_path):
    """A resumed run re-enters its own task; treating that as contention would strand it."""
    run = tmp_path / "run"
    codes = _race(run, lambda n: ("claim", "T-1", "--actor", "same-worker"))
    assert codes.count(0) == WORKERS, "re-claiming is idempotent for one actor"


@pytest.mark.parametrize("verb", ["claim", "slot-acquire"])
def test_the_race_harness_can_actually_observe_a_failure(tmp_path, verb, monkeypatch):
    """Prove the tests above are measuring something.

    Point every contender at its OWN run directory and the mutex has nothing to
    coordinate — all eight then win. If that does not happen, the race is not being run
    and the assertions above would pass without testing anything.
    """
    args = (
        (lambda n: ("claim", "T-1", "--actor", f"w{n}"))
        if verb == "claim"
        else (lambda n: ("slot-acquire", "--holder", f"w{n}"))
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        codes = list(
            pool.map(lambda n: _cli(tmp_path / f"run-{n}", *args(n)), range(WORKERS))
        )
    assert codes.count(0) == WORKERS, (
        "with each process given its own store there is nothing to contend for; "
        "if this fails the race harness is not exercising the mutex at all"
    )
