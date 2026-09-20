"""Run N commands at once, bounded, each with a timeout, and answer for every one.

WHAT THIS REPLACES. `/swarm` step 5 says "run all n in ONE message as background Bash
calls — otherwise they run sequentially and you have gained nothing"; step 7 says the
same of the lenses. The orchestrator then reads each result's rc file, one call each.
The headless orchestrator (`campaign-orchestrator.md`) could not even wait: a headless
session ends the moment it stops calling tools, so it was told to write
`until [ -s <path> ]; do sleep 20; done` and "repeat the call if it times out" — the LLM
as the scheduler, one turn per timeout at the strong tier. `dispatch.sh` already knew
every `--out` path it had been given.

TWO SHAPES, ONE ENGINE. `run_jobs` runs the batch in-process under a thread pool and
returns when every job has answered — for a caller that is itself a script (`lens_gate`)
or a terminal orchestrator whose Bash call can outlive the batch. `detach` + `wait` are
for the headless orchestrator, whose Bash call is capped (~10 minutes) while a wave of
workers runs longer: `detach` starts a supervisor in its own session that runs the same
engine and writes each job's rc/out/err as it finishes; `wait` blocks up to its timeout
and exits 5 while any job is still running — so the orchestrator makes the same one call
until it exits 0 or 1, and never writes a sleep loop.

A HANG IS AN OUTCOME. Every job has a timeout; a job still running at it is killed and
reported HUNG, never left to hold the wave. The recorded 8.5-hour stall was a command
blocking on a permission prompt in another session.

NO SHELL. A job line is split with `shlex`; a token that is a shell operator (`;`, `&&`,
`||`, `|`, a redirection) is refused, because the same text run by a shell would mean
something this engine does not do — and a compound is exactly what the permission rules
cannot match.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from .resolve import REPO

OK, FAIL, HUNG = "ok", "fail", "hung"
#: `fanout.sh --wait` exit when a job is still running: call again with the same id.
EXIT_RUNNING = 5
#: A job that has produced nothing in this long is stuck. A worker dispatch at its
#: ceiling runs 30-40 minutes; a lens 5-15. Generous, and per job.
DEFAULT_TIMEOUT = 3600
SHELL_TOKENS = frozenset({";", "&&", "||", "|", ">", ">>", "<", "&"})
FULL = "full: "


@dataclass(frozen=True)
class Job:
    name: str
    argv: tuple[str, ...]
    cwd: str
    timeout: int = DEFAULT_TIMEOUT
    task: str | None = None


@dataclass
class JobResult:
    name: str
    argv: tuple[str, ...]
    status: str
    rc: int | None
    seconds: int
    stdout: str = ""
    stderr: str = ""
    task: str | None = None

    @property
    def first_line(self) -> str:
        for ln in self.stdout.splitlines():
            if ln.strip() and not ln.startswith(FULL) and not ln.startswith("... "):
                return ln.strip()[:200]
        return ""

    @property
    def full_path(self) -> str | None:
        """The `full: <path> (<n> lines)` line `dispatch.sh --digest` prints."""
        for ln in reversed(self.stdout.splitlines()):
            i = ln.find(FULL)
            if i >= 0:
                rest = ln[i + len(FULL):].strip()
                return rest.split(" (")[0].strip()
        return None

    def line(self) -> str:
        mark = {OK: "ok  ", FAIL: "FAIL", HUNG: "HUNG"}[self.status]
        head = f"[{mark}] {self.name}"
        how = f"exit {self.rc}" if self.rc is not None else f"killed after {self.seconds}s"
        tail = self.first_line
        full = self.full_path
        return f"{head:<40} {how}  {self.seconds}s  {tail}" + (f"\n{'':<40} full: {full}" if full else "")


def run_one(job: Job, runner=None) -> JobResult:
    run = runner or subprocess.run
    started = time.monotonic()
    try:
        proc = run(list(job.argv), cwd=job.cwd, capture_output=True, text=True, timeout=job.timeout)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return JobResult(job.name, job.argv, HUNG, None, int(time.monotonic() - started), out, err, job.task)
    except OSError as exc:
        return JobResult(job.name, job.argv, FAIL, None, int(time.monotonic() - started), "", str(exc), job.task)
    status = OK if proc.returncode == 0 else FAIL
    return JobResult(job.name, job.argv, status, proc.returncode, int(time.monotonic() - started), proc.stdout or "", proc.stderr or "", job.task)


def run_jobs(jobs: list[Job], cap: int = 4, runner=None, on_done=None) -> list[JobResult]:
    """Every job, at most `cap` at once, results in the jobs' order. Every job answers."""
    results: list[JobResult | None] = [None] * len(jobs)
    with ThreadPoolExecutor(max_workers=max(1, cap)) as pool:
        futures = {pool.submit(run_one, j, runner): i for i, j in enumerate(jobs)}
        for fut, i in futures.items():
            results[i] = fut.result()
            if on_done:
                on_done(results[i])
    return [r for r in results if r is not None]


# --- job files ------------------------------------------------------------------------


def parse_jobs(text: str, cwd: str, timeout: int) -> tuple[list[Job], list[str]]:
    """One command per line, `#` comments. Returns the jobs and every reason to refuse."""
    jobs: list[Job] = []
    problems: list[str] = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "$(" in line or "`" in line:
            problems.append(f"line {n}: command substitution — a job is one command, no shell")
            continue
        try:
            argv = shlex.split(line)
            # The operator scan uses shlex's punctuation mode, which splits `a;` into
            # `a`, `;` and leaves a QUOTED operator inside its word — so `'a && b'` is
            # data and `a && b` is shell.
            lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
            lexer.whitespace_split = True
            operators = [t for t in lexer if t in SHELL_TOKENS or (set(t) <= set(";&|<>") and t)]
        except ValueError as exc:
            problems.append(f"line {n}: {exc}")
            continue
        if operators:
            problems.append(f"line {n}: shell operator {operators[0]!r} — a job is one command, no shell")
            continue
        task = None
        if "--task" in argv:
            i = argv.index("--task")
            task = argv[i + 1] if i + 1 < len(argv) else None
        name = task or Path(argv[0]).name
        jobs.append(Job(name=f"{n}:{name}", argv=tuple(argv), cwd=cwd, timeout=timeout, task=task))
    return jobs, problems


# --- detach / wait ---------------------------------------------------------------------


def fanout_dir(base: Path | None = None) -> Path:
    if base is not None:
        return base / "fanout"
    from tracker.locks import run_dir
    from tracker.port import TrackerError

    try:
        return run_dir() / "fanout"
    except TrackerError:
        return REPO / ".harness" / "run" / "fanout"


def detach(jobs: list[Job], cap: int, base: Path | None = None, popen=None) -> str:
    """Start the supervisor and return the run id. The supervisor is `python -m
    models.fanout --supervise <dir>` in its own session, so the caller's Bash call
    ending does not end the wave."""
    root = fanout_dir(base)
    root.mkdir(parents=True, exist_ok=True)
    run_id = time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}"
    d = root / run_id
    d.mkdir()
    (d / "jobs.json").write_text(json.dumps({"cap": cap, "jobs": [asdict(j) for j in jobs]}, indent=2) + "\n")
    start = popen or subprocess.Popen
    with open(d / "supervisor.log", "ab") as log:
        start(
            [sys.executable, "-m", "models.fanout", "--supervise", str(d)],
            cwd=str(Path(__file__).resolve().parent.parent), stdout=log, stderr=log,
            stdin=subprocess.DEVNULL, start_new_session=True,
            env={**os.environ, "MAD_HARNESS_CALLER_PWD": os.environ.get("MAD_HARNESS_CALLER_PWD", str(REPO))},
        )
    return run_id


def supervise(d: Path) -> int:
    """The detached side: run the jobs, write each result as it lands, then `done`."""
    spec = json.loads((d / "jobs.json").read_text())
    jobs = [Job(**j) if isinstance(j.get("argv"), tuple) else Job(**{**j, "argv": tuple(j["argv"])}) for j in spec["jobs"]]

    def on_done(r: JobResult) -> None:
        i = jobs.index(next(j for j in jobs if j.name == r.name))
        (d / f"job-{i}.out").write_text(r.stdout)
        (d / f"job-{i}.err").write_text(r.stderr)
        (d / f"job-{i}.json").write_text(json.dumps({**asdict(r), "argv": list(r.argv)}) + "\n")
        (d / f"job-{i}.rc").write_text(f"{r.rc if r.rc is not None else 'hung'}\n")

    run_jobs(jobs, spec.get("cap", 4), on_done=on_done)
    (d / "done").write_text(time.strftime("%Y-%m-%dT%H:%M:%S") + "\n")
    return 0


def wait(run_id: str, timeout: int, base: Path | None = None, poll: float = 2.0) -> tuple[bool, list[JobResult], list[Job]]:
    """Block up to `timeout` seconds. (done, results so far, jobs)."""
    d = fanout_dir(base) / run_id
    spec = json.loads((d / "jobs.json").read_text())
    jobs = [Job(**{**j, "argv": tuple(j["argv"])}) for j in spec["jobs"]]
    deadline = time.monotonic() + timeout
    while True:
        done = (d / "done").exists()
        if done or time.monotonic() >= deadline:
            break
        time.sleep(poll)
    results: list[JobResult] = []
    for i, j in enumerate(jobs):
        f = d / f"job-{i}.json"
        if f.exists():
            raw = json.loads(f.read_text())
            raw["argv"] = tuple(raw["argv"])
            results.append(JobResult(**raw))
    return (d / "done").exists(), results, jobs


# --- the manifest hook -----------------------------------------------------------------


def record_dispatched(manifest: str, results: list[JobResult]) -> None:
    from . import wave_manifest

    path = wave_manifest.path_for(manifest)
    for r in results:
        if not r.task:
            continue
        agent = next((a for a in r.argv if a in ("fullstack-engineer", "quality-engineer", "fidelity-auditor")), None) or (r.argv[1] if len(r.argv) > 1 else "?")
        wave_manifest.append(path, "dispatched", {"agent": agent, "exit": r.rc, "status": r.status, "first_line": r.first_line, "full": r.full_path}, task=r.task)


# --- the command ----------------------------------------------------------------------


def report(results: list[JobResult], jobs: list[Job], done: bool) -> tuple[str, int]:
    lines = [r.line() for r in results]
    answered = {r.name for r in results}
    for j in jobs:
        if j.name not in answered:
            lines.append(f"[....] {j.name:<34} still running")
    n_fail = sum(1 for r in results if r.status != OK)
    if not done:
        lines.append(f"\n{len(results)} of {len(jobs)} answered; still running — call --wait again with the same id (exit {EXIT_RUNNING})")
        return "\n".join(lines), EXIT_RUNNING
    lines.append(f"\n{len(results)} job(s): {len(results) - n_fail} ok, {n_fail} not ok" + (" — read the [FAIL]/[HUNG] lines" if n_fail else ""))
    return "\n".join(lines), (1 if n_fail else 0)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="fanout.sh",
        description="Run the commands in a jobs file concurrently, each with a timeout, and answer for every one. --detach starts them in a supervisor; --wait <id> blocks up to --timeout and exits 5 while any is still running.",
    )
    ap.add_argument("--jobs", help="a file: one command per line, # comments, no shell")
    ap.add_argument("--cap", type=int, default=4, help="at most this many at once")
    ap.add_argument("--timeout", type=int, default=None, help="per job (run/detach: seconds before HUNG, default 3600) or for --wait (seconds to block, default 540)")
    ap.add_argument("--detach", action="store_true", help="start a supervisor and print the run id")
    ap.add_argument("--wait", metavar="RUN_ID", help="block for a detached run")
    ap.add_argument("--wave", help="record `dispatched` on this wave manifest (<epic>-w<n>)")
    ap.add_argument("--supervise", metavar="DIR", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args.supervise:
        return supervise(Path(args.supervise))

    if args.wait:
        done, results, jobs = wait(args.wait, args.timeout or 540)
        text, code = report(results, jobs, done)
        print(text)
        if done and args.wave:
            record_dispatched(args.wave, results)
        return code

    if not args.jobs:
        ap.error("--jobs <file>, or --wait <id>")
    try:
        text = Path(args.jobs).read_text()
    except OSError as exc:
        print(f"cannot read {args.jobs}: {exc}", file=sys.stderr)
        return 2
    cwd = os.environ.get("MAD_HARNESS_CALLER_PWD") or str(REPO)
    jobs, problems = parse_jobs(text, cwd, args.timeout or DEFAULT_TIMEOUT)
    if problems:
        print(f"REFUSED — {len(problems)} problem(s); nothing started:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2
    if not jobs:
        print("REFUSED: no commands in the jobs file", file=sys.stderr)
        return 2

    if args.detach:
        run_id = detach(jobs, args.cap)
        print(f"run: {run_id}")
        print(f"{len(jobs)} job(s) started under a supervisor; `fanout.sh --wait {run_id} --timeout 540` — exit 5 means still running, call it again")
        return 0

    results = run_jobs(jobs, args.cap)
    text, code = report(results, jobs, True)
    print(text)
    if args.wave:
        record_dispatched(args.wave, results)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
