"""Integrate a wave — `/swarm` step 8 as one call: merge in order, gate once, attribute red.

WHAT THE ORCHESTRATOR DID BY HAND, inside a lock nothing times out: `slot-acquire`; for
each passing branch, in ascending task-id order, `git merge` FROM THE REF (never from a
worktree — one was found holding a staged revert of its own fix while `git log` on the
branch showed the good commit) and `git rev-parse HEAD`; `slot-release`; one
`run.sh --stack <s> lint typecheck test` per declared stack, issued in a single message so
they run concurrently; on red, `git log --oneline -- <failing path>` to name the task; on a
conflict, `git merge --abort` and leave the branch unmerged. Eight to twelve calls at the
orchestrator's context price, and two recorded failure modes: the slot left held when a
merge failed mid-sequence (nothing times it out — `/halt` §4 exists for it), and the
bisection spelled in prose with `git reset --hard` in it.

THE SLOT IS RELEASED IN A `finally`. Whatever happens between acquire and release — a
conflict, a merge that raises, a keyboard interrupt — the slot is not left for the next
wave to find held.

A CONFLICT IS NEVER RESOLVED HERE. It is a planning defect first — step 3's contention
check missed a shared path — and the two authors are the worst parties to arbitrate.
The merge is aborted, the branch left unmerged with the conflicting paths named, and the
rest of the wave continues; the orchestrator re-queues the task on top of the merged
result (rebase-and-retry is almost always cheaper than a resolution) or, only when a
re-queue would lose real work, resolves it as the neutral party and sends the result
through every lens. That decision is the orchestrator's; the script hands it the facts.

GATE ONCE, ON THE MERGED RESULT. Per-merge gating certifies states that are never pushed
and do not survive the wave; `M1+M2+M3` is the only state that ships, and it is gated
identically. A stack with every key undeclared is FAIL "nothing measured", never green.
Red is ATTRIBUTED, not fixed: the failing paths from the gate's own digest → the commits
that touched them since the wave base → the task ids their subjects name. The revert is
suggested (`git revert -m 1 <merge sha>`) and never performed.
"""

from __future__ import annotations

import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .resolve import HARNESS, REPO
from .steps import (
    FAIL,
    INFO,
    OK,
    REMOTE_TIMEOUT,
    Raw,
    Result,
    execute,
    failure_detail,
    render,
    tail,
)

TK = HARNESS / "tracker" / "tk.sh"
RUN = HARNESS / "verify" / "run.sh"
#: The task id a worker branch is for: `harness-w3-PROJ-4f2a` → `PROJ-4f2a`.
BRANCH_TASK = re.compile(r"^(?:harness-w\d+-|worktree-agent-)(?P<task>.+)$")
#: A source path in a failure digest.
FAIL_PATH = re.compile(r"(?<![\w./-])((?:[\w.-]+/)*[\w.-]+\.(?:py|ts|tsx|js|jsx|go|rb|rs|java|kt))(?![\w/])")
GATE_KEYS = ("lint", "typecheck", "test")
EXIT_OK, EXIT_RED, EXIT_PRECONDITION = 0, 1, 2


def task_of(branch: str) -> str:
    m = BRANCH_TASK.match(branch)
    return m.group("task") if m else branch


def sort_key(branch: str) -> tuple:
    t = task_of(branch)
    return tuple((int(x) if x.isdigit() else x) for x in re.split(r"(\d+)", t))


def preconditions(branches: list[str], runner, cwd: str) -> tuple[list[Result], str | None]:
    """Every branch is a ref; the tree is clean. Refused BEFORE the slot is taken."""
    out: list[Result] = []
    for b in branches:
        raw = execute(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{b}"], cwd=cwd, runner=runner)
        if not raw.ran or raw.returncode != 0:
            out.append(Result(f"ref {b}", FAIL, "not a branch in this repository — a typo, or a ref the sweep deleted; nothing merged"))
        else:
            out.append(Result(f"ref {b}", OK, raw.stdout.strip()[:12]))
    raw = execute(["git", "status", "--porcelain"], cwd=cwd, runner=runner)
    dirty = [ln for ln in (raw.stdout if raw.ran else "").splitlines() if ln.strip()]
    if not raw.ran or raw.returncode != 0 or dirty:
        out.append(Result("git status --porcelain", FAIL, f"{len(dirty)} path(s) dirty — a merge onto a dirty tree loses work:\n" + "\n".join(dirty[:8]) if dirty else failure_detail(raw)))
    else:
        out.append(Result("git status --porcelain", OK, "clean"))
    head = execute(["git", "rev-parse", "HEAD"], cwd=cwd, runner=runner)
    base = head.stdout.strip() if head.ran and head.returncode == 0 else None
    return out, base


def merge_all(branches: list[str], holder: str, runner, cwd: str, dry_run: bool) -> tuple[list[Result], list[dict], list[dict]]:
    """Acquire → merge each from the ref, ascending → release, in a finally."""
    results: list[Result] = []
    merged: list[dict] = []
    conflicts: list[dict] = []
    raw = execute([str(TK), "slot-acquire", "--holder", holder], cwd=cwd, runner=runner)
    if not raw.ran or raw.returncode != 0:
        state = execute([str(TK), "slot-check", "--json"], cwd=cwd, runner=runner)
        who = ""
        try:
            st = json.loads(state.stdout.strip().splitlines()[-1]) if state.ran and state.returncode == 0 else {}
            who = f"held by {st.get('holder') or '?'}" + (" — STALE, `tk.sh slot-release --force` releases it" if st.get("stale") else " — alive; wait for it, or /halt it")
        except (ValueError, IndexError):
            who = failure_detail(raw)
        results.append(Result("tk.sh slot-acquire", FAIL, f"the merge slot is not free: {who}", raw))
        return results, merged, conflicts
    results.append(Result("tk.sh slot-acquire", OK, holder, raw))
    try:
        for b in sorted(branches, key=sort_key):
            name = f"merge {b}"
            if dry_run:
                results.append(Result(name, INFO, "would merge (--dry-run)"))
                continue
            raw = execute(["git", "merge", "--no-ff", "--no-edit", b], cwd=cwd, runner=runner)
            if not raw.ran:
                # A git that hung or vanished is not a conflict; nothing after it is safe.
                results.append(Result(name, FAIL, f"git merge did not run — {failure_detail(raw)}; the remaining branches were not attempted", raw))
                break
            if raw.returncode == 0:
                sha = execute(["git", "rev-parse", "HEAD"], cwd=cwd, runner=runner)
                merged.append({"task": task_of(b), "branch": b, "merge_sha": sha.stdout.strip() if sha.ran else None})
                results.append(Result(name, OK, f"{(sha.stdout.strip()[:12] if sha.ran else '?')}  ({task_of(b)})", raw))
                continue
            paths_raw = execute(["git", "diff", "--name-only", "--diff-filter=U"], cwd=cwd, runner=runner)
            paths = [ln.strip() for ln in (paths_raw.stdout if paths_raw.ran else "").splitlines() if ln.strip()]
            execute(["git", "merge", "--abort"], cwd=cwd, runner=runner)
            conflicts.append({"task": task_of(b), "branch": b, "paths": paths})
            results.append(Result(name, FAIL, f"CONFLICT in {', '.join(paths[:4]) or 'unknown paths'} — aborted, left unmerged. A conflict is a step-3 planning miss: re-queue {task_of(b)} on top of the merged result, or resolve it yourself as the neutral party and send it through every lens.", raw))
    finally:
        rel = execute([str(TK), "slot-release", "--holder", holder], cwd=cwd, runner=runner)
        results.append(Result("tk.sh slot-release", OK if (rel.ran and rel.returncode == 0) else FAIL, "released" if (rel.ran and rel.returncode == 0) else f"NOT released — `tk.sh slot-release --holder {holder} --force`:\n{failure_detail(rel)}", rel))
    return results, merged, conflicts


def gate(stacks: list[str], lane: str | None, runner, cwd: str, project) -> tuple[list[Result], dict]:
    """One `run.sh` per stack, concurrently, never split further — type checkers and
    bundlers each hold 1-2 GB. Every key answers; an undeclared key is `--`, a stack
    with EVERY key undeclared is FAIL 'nothing measured'."""
    if not stacks:
        from .commands import stacks_for

        stacks = [s.name for s in stacks_for(lane, project)]
    if not stacks:
        return [Result("wave gate", FAIL, "no stacks declared — nothing measured, and nothing measured is not green")], {"status": "red", "stacks": {}, "attributed": []}

    def one(stack: str) -> tuple[str, Raw]:
        return stack, execute([str(RUN), "--stack", stack, *GATE_KEYS], cwd=cwd, timeout=REMOTE_TIMEOUT * 4, runner=runner)

    with ThreadPoolExecutor(max_workers=len(stacks)) as pool:
        outs = list(pool.map(one, stacks))
    results: list[Result] = []
    per: dict = {}
    red = False
    for stack, raw in outs:
        name = f"run.sh --stack {stack} {' '.join(GATE_KEYS)}"
        keys: dict[str, str] = {}
        for k in GATE_KEYS:
            m = re.search(rf"^\s*\[(\S+?)\s*\]\s+{re.escape(stack)}:{k}\b", raw.stdout or "", re.M)
            keys[k] = (m.group(1).lower() if m else "?")
        per[stack] = keys
        measured = [k for k, v in keys.items() if v not in ("--", "absent", "?")]
        if not raw.ran:
            red = True
            results.append(Result(name, FAIL, failure_detail(raw), raw))
        elif not measured:
            red = True
            results.append(Result(name, FAIL, f"nothing measured — every key undeclared or unread ({keys}); a gate that ran nothing is not green:\n{tail(raw.stdout)}", raw))
        elif raw.returncode != 0:
            red = True
            results.append(Result(name, FAIL, f"RED — {keys}\n{tail(raw.stdout, 6)}", raw))
        else:
            results.append(Result(name, OK, f"green — {keys}", raw))
    return results, {"status": "red" if red else "green", "stacks": per, "attributed": []}


def attribute(results: list[Result], merged: list[dict], base: str | None, runner, cwd: str) -> tuple[list[Result], list[str]]:
    """Red → the failing paths in the gate's digest → the commits since the wave base that
    touched them → the task ids their subjects name. Zero extra suite runs."""
    paths: list[str] = []
    for r in results:
        if r.status == FAIL and r.raw is not None:
            for m in FAIL_PATH.finditer((r.raw.stdout or "") + "\n" + (r.raw.stderr or "")):
                p = m.group(1)
                if p not in paths and (Path(cwd) / p).exists():
                    paths.append(p)
    if not paths or not base:
        return [Result("attribution", INFO, "no failing path found in the gate's digest — read the run logs; `git log --oneline <base>..HEAD -- <path>` names the task")], []
    tasks = [m["task"] for m in merged]
    blamed: list[str] = []
    lines = []
    for p in paths[:8]:
        raw = execute(["git", "log", "--format=%s", f"{base}..HEAD", "--", p], cwd=cwd, runner=runner)
        subjects = (raw.stdout if raw.ran else "")
        hits = [t for t in tasks if re.search(rf"(?<![\w.]){re.escape(t)}(?![\w])", subjects)]
        for t in hits:
            if t not in blamed:
                blamed.append(t)
        lines.append(f"{p} ← {', '.join(hits) or 'no merged task touched it since the base'}")
    detail = "\n".join(lines) + ("\n\nsuggested, not done: `git revert -m 1 <merge sha>` for the culprit, re-run the gate once, file a fix task against that id" if blamed else "")
    return [Result("attribution", INFO if blamed else FAIL, detail)], blamed


def run(
    branches: list[str],
    *,
    lane: str | None = None,
    stacks: list[str] | None = None,
    wave: str | None = None,
    dry_run: bool = False,
    project=None,
    runner=None,
    cwd: str | None = None,
    holder: str | None = None,
) -> tuple[str, int, dict]:
    at = cwd or str(REPO)
    holder = holder or f"merge-wave-{os.getpid()}"
    pre, base = preconditions(branches, runner, at)
    results = list(pre)
    facts: dict = {"wave_base": base, "merged": [], "conflicts": [], "gate": None}
    if any(r.status == FAIL for r in pre):
        return _report(results, EXIT_PRECONDITION, facts), EXIT_PRECONDITION, facts

    merges, merged, conflicts = merge_all(branches, holder, runner, at, dry_run)
    results += merges
    facts.update(merged=merged, conflicts=conflicts)
    if any(r.name == "tk.sh slot-acquire" and r.status == FAIL for r in merges):
        return _report(results, EXIT_RED, facts), EXIT_RED, facts
    if dry_run:
        results.append(Result("wave gate", INFO, "skipped (--dry-run)"))
        return _report(results, EXIT_OK, facts), EXIT_OK, facts

    gate_results, gate_facts = gate(list(stacks or []), lane, runner, at, project)
    results += gate_results
    if gate_facts["status"] == "red":
        attr, blamed = attribute(gate_results, merged, base, runner, at)
        results += attr
        gate_facts["attributed"] = blamed
    facts["gate"] = gate_facts

    if wave:
        try:
            from . import wave_manifest

            path = wave_manifest.path_for(wave)
            for m in merged:
                wave_manifest.append(path, "merged", m)
            for c in conflicts:
                wave_manifest.append(path, "conflicts", c)
            wave_manifest.set_key(path, "gate", gate_facts)
            wave_manifest.heartbeat(path, "GATE", f"{gate_facts['status']} merged={len(merged)} conflicts={len(conflicts)}", runner=runner)
            results.append(Result("manifest", INFO, f"{path.name}: {len(merged)} merged, {len(conflicts)} conflict(s), gate {gate_facts['status']}"))
        except (OSError, ValueError) as exc:
            results.append(Result("manifest", INFO, f"not recorded — {exc}"))
    else:
        results.append(Result("manifest", INFO, "none (no --wave)"))

    code = EXIT_RED if (gate_facts["status"] == "red" or conflicts) else EXIT_OK
    return _report(results, code, facts), code, facts


def _report(results: list[Result], code: int, facts: dict) -> str:
    out = ["merge-wave", render(results)]
    if code == EXIT_PRECONDITION:
        out.append("\nREFUSED before the slot was taken — resolve the [FAIL] line(s) and run again; nothing merged.")
    elif code == EXIT_OK:
        out.append(f"\nGREEN — {len(facts['merged'])} branch(es) merged, the wave gate passed on the merged result. Next: the wave-stage code review, then close-wave.sh.")
    else:
        why = []
        if facts.get("conflicts"):
            why.append(f"{len(facts['conflicts'])} conflict(s) left unmerged")
        if (facts.get("gate") or {}).get("status") == "red":
            why.append("the wave gate is RED")
        out.append(f"\nNOT LANDED — {' and '.join(why) or 'see above'}. The merged commits are on HEAD; revert or fix, then re-run the gate: `run.sh --stack <s> lint typecheck test`.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .project import ProjectError, load

    ap = argparse.ArgumentParser(prog="merge-wave.sh", description="/swarm step 8 as one call: every branch a ref and the tree clean (before the slot), slot-acquire, merge each FROM THE REF ascending by task id (a conflict is aborted and left unmerged), slot-release in a finally, one run.sh per stack concurrently, red attributed by git log. Never resolves a conflict, never reverts.")
    ap.add_argument("branches", nargs="+")
    ap.add_argument("--lane", default=None, help="the lane whose stacks gate the wave")
    ap.add_argument("--stack", action="append", default=None, help="gate this stack (repeatable); default: the lane's, or every declared stack")
    ap.add_argument("--wave", help="record merged/conflicts/gate on this wave manifest")
    ap.add_argument("--dry-run", action="store_true", help="preconditions and the slot only; merge nothing")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        project = load()
    except ProjectError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    text, code, facts = run(args.branches, lane=args.lane, stacks=args.stack, wave=args.wave, dry_run=args.dry_run, project=project)
    print(text)
    if args.json:
        print(json.dumps(facts))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
