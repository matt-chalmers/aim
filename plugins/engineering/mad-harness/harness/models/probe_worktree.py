"""Prove a worker's worktree works — `/harness-setup` §7's by-hand probe as one call.

WHAT THIS REPLACES. The last step of `/harness-setup` was prose: "create a scratch
worktree, run `swarm-worktree-init.sh 1 <lane>` inside it, confirm `.swarm-env` sources
cleanly and names a per-worker database, and remove the worktree." Four to five steps with
a cleanup at the end, run by the most expensive caller in the system, and non-idempotent:
a probe abandoned after its init left a worktree that `git add -A` then committed, and a
probe that "confirmed" the env file by reading it never sourced it — the shell error a
worker would hit sat in a value the eye read as fine. Every check here is mechanical;
nothing in this step was ever judgement.

WHAT IT CHECKS, IN ORDER. A detached scratch worktree under the same root the dispatcher
uses (so the bootstrap sees the layout a real worker gets); the init script run FROM
INSIDE it, as a worker's would be — it refuses in the primary, which is itself a fact this
proves; `.swarm-env` parses (`bash -n`) and sources (`bash -c '. ./.swarm-env'`), which
catches the unquoted value that reads fine and fails a real shell; the identity lines the
tracker's claim needs (`TRACKER_ACTOR=swarm-w<n>`, `SWARM_LANE=<lane>`); and every
per-worker variable a stack declares — one whose template carries `{worker}` — present
with the worker's number substituted, or the config has two workers sharing a database.
A project whose stacks declare no per-worker variable is told so rather than passed
silently: that may be right (a Node-only stack) or the omission the probe exists to find.

THE WORKTREE IS ALWAYS REMOVED, in a `finally`, unless `--keep` asks for it to stay for
inspection — and then its path is printed, never left to be found by the next sweep.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from .project import ProjectError, load
from .resolve import HARNESS, REPO
from .steps import FAIL, INFO, OK, Result, execute, failure_detail, render, tail

INIT = HARNESS / "swarm" / "swarm-worktree-init.sh"
WORKTREE_ROOT = REPO / ".claude" / "worktrees"
EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2


def probe_path(worker: int, root: Path = WORKTREE_ROOT) -> Path:
    return root / f"probe-w{worker}-{os.getpid()}"


def per_worker_vars(project, worker: int) -> dict[str, str]:
    """Every stack env var whose template names the worker, with the value this worker
    should see. Empty when no stack declares one — reported, not assumed fine."""
    out: dict[str, str] = {}
    for st in project.stacks:
        for var, template in (st.env or {}).items():
            if "{worker}" in str(template):
                out[var] = str(template).format(slug=project.slug, worker=worker)
    return out


def sourced_env(envfile: Path, runner, cwd: str) -> tuple[dict[str, str], Result]:
    """Source the file in a fresh shell and read back what it exported. The file's
    own directory is the cwd, as a worker's runner would have it."""
    raw = execute(["bash", "-n", str(envfile)], cwd=cwd, runner=runner)
    if not raw.ran or raw.returncode != 0:
        return {}, Result("bash -n .swarm-env", FAIL, failure_detail(raw), raw)
    script = f"set -e; . {shlex.quote(str(envfile))}; env -0"
    raw = execute(["bash", "-c", script], cwd=cwd, runner=runner)
    if not raw.ran or raw.returncode != 0:
        return {}, Result("source .swarm-env", FAIL, failure_detail(raw), raw)
    env: dict[str, str] = {}
    for entry in raw.stdout.split("\0"):
        k, sep, v = entry.partition("=")
        if sep:
            env[k] = v
    return env, Result("source .swarm-env", OK, f"{len(env)} variables after sourcing", raw)


def check_env(env: dict[str, str], worker: int, lane: str, expected: dict[str, str]) -> list[Result]:
    out: list[Result] = []
    actor = env.get("TRACKER_ACTOR")
    if actor == f"swarm-w{worker}":
        out.append(Result("TRACKER_ACTOR", OK, actor))
    else:
        out.append(Result("TRACKER_ACTOR", FAIL, f"{actor!r} — the tracker's claim needs swarm-w{worker}"))
    got_lane = env.get("SWARM_LANE")
    if got_lane == lane:
        out.append(Result("SWARM_LANE", OK, lane))
    else:
        out.append(Result("SWARM_LANE", FAIL, f"{got_lane!r}, expected {lane!r}"))
    if not expected:
        out.append(
            Result(
                "per-worker variable",
                INFO,
                "no stack declares one (an env value carrying {worker}) — two workers would "
                "share every resource; right for a stack with none, wrong for one with a database",
            )
        )
    for var, want in sorted(expected.items()):
        got = env.get(var)
        if got == want:
            out.append(Result(var, OK, want))
        elif got is None:
            out.append(Result(var, FAIL, f"missing — the stack declares it as {want!r}"))
        else:
            out.append(Result(var, FAIL, f"{got!r}, expected {want!r} — not per-worker"))
    return out


def run(
    *,
    lane: str,
    worker: int = 1,
    keep: bool = False,
    project=None,
    runner=None,
    cwd: str | None = None,
    root: Path = WORKTREE_ROOT,
) -> tuple[list[Result], int]:
    cwd = cwd or str(REPO)
    results: list[Result] = []
    try:
        p = project or load()
    except ProjectError as exc:
        return [Result("harness.yaml", FAIL, str(exc))], EXIT_USAGE
    lanes = p.raw.get("lanes") or {}
    if lanes and lane not in lanes:
        return [Result("lane", FAIL, f"{lane!r} is not declared; lanes: {', '.join(lanes)}")], EXIT_USAGE

    wt = probe_path(worker, root)
    raw = execute(["git", "worktree", "add", "--detach", str(wt), "HEAD"], cwd=cwd, runner=runner)
    if not raw.ran or raw.returncode != 0:
        results.append(Result("git worktree add", FAIL, failure_detail(raw), raw))
        return results, EXIT_FAILED
    results.append(Result("git worktree add", OK, str(wt), raw))

    code = EXIT_OK
    try:
        code = _inside(wt, worker, lane, p, results, runner)
    finally:
        # A `return` inside the try has already bound its value; the removal's verdict
        # must be folded in here, which is why the probe body is its own function.
        if keep:
            results.append(Result("worktree kept", INFO, f"{wt} — remove it with `git worktree remove --force {wt}`"))
        else:
            raw = execute(["git", "worktree", "remove", "--force", str(wt)], cwd=cwd, runner=runner)
            if raw.ran and raw.returncode == 0:
                results.append(Result("git worktree remove", OK, "scratch worktree removed", raw))
            else:
                results.append(Result("git worktree remove", FAIL, failure_detail(raw), raw))
                code = EXIT_FAILED
    return results, code


def _inside(wt: Path, worker: int, lane: str, project, results: list[Result], runner) -> int:
    """The checks that run once the worktree exists. Appends to `results`; returns the code."""
    raw = execute([str(INIT), str(worker), lane], cwd=str(wt), timeout=600, runner=runner)
    if not raw.ran or raw.returncode != 0:
        results.append(Result("swarm-worktree-init.sh", FAIL, failure_detail(raw), raw))
        return EXIT_FAILED
    results.append(Result("swarm-worktree-init.sh", OK, tail(raw.stdout, 2), raw))

    envfile = wt / ".swarm-env"
    if not envfile.exists():
        results.append(Result(".swarm-env", FAIL, f"the init returned 0 but wrote no {envfile}"))
        return EXIT_FAILED
    env, res = sourced_env(envfile, runner, str(wt))
    results.append(res)
    if res.status == FAIL:
        return EXIT_FAILED
    checks = check_env(env, worker, lane, per_worker_vars(project, worker))
    results.extend(checks)
    return EXIT_FAILED if any(r.status == FAIL for r in checks) else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="probe-worktree.sh",
        description="Create a scratch worker worktree, run the init inside it, prove .swarm-env, remove it.",
    )
    ap.add_argument("lane", nargs="?", help="the lane to init for (default: the project's first)")
    ap.add_argument("--worker", type=int, default=1)
    ap.add_argument("--keep", action="store_true", help="leave the worktree for inspection")
    args = ap.parse_args(argv)
    lane = args.lane
    if lane is None:
        from .worker import default_lane

        lane = default_lane()
    results, code = run(lane=lane, worker=args.worker, keep=args.keep)
    print(render(results))
    if code == EXIT_OK:
        print(f"\nOK — a worker in lane {lane} gets a worktree it can use")
    elif code == EXIT_FAILED:
        print("\nFAILED — a config that validates but produces a worktree a worker cannot use has not been tested")
    return code


if __name__ == "__main__":
    sys.exit(main())
