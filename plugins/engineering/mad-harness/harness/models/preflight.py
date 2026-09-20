"""The campaign's pre-flight — `campaign-loop` §0's mechanical gates, one call.

MEASURED. A field campaign orchestrator ran 237 requests with its context growing
55k -> 920k tokens, ~380k on average — so each of §0's six shell lines, run as its own
tool call, re-read ~380k tokens to learn one exit status: ~$0.11-0.17 per line, ~6x what
a worker pays for the same call. The gates are deterministic; only their VERDICT needs
the orchestrator. This runs them in the loop's order and returns the verdict in one
result: one line per step, a summary, and an exit status the loop can branch on.

    0  all gates passed — proceed to §1
    3  the config predates the installed plugin (`check-project-config.sh --strict`
       said UPGRADE) — stop, in both modes, and have the owner run /harness-setup
    1  anything else failed — a dirty tree, a held merge slot, a check that errored

THE WRITES WAIT FOR THE GATES. `tk.sh autosync off`, `git worktree prune`, the sweep's
`--apply` and the stack-command repair are the steps that change anything, and the loop's
own text stops at a dirty tree or an UPGRADE before reaching any of them. So it does here
too: when a gate before a write fails, the write is reported `[skip]` rather than
performed — a pre-flight that fails must leave the tracker, the worktrees and the config
exactly as it found them, or the run that never started still needs a `/halt` to undo it.

0.10.20: THE FOUR LINES AFTER THE ONE CALL JOIN IT. `/swarm` step 1 and `campaign-loop` §0
still had the orchestrator run `git worktree prune`, `worktree-sweep.sh` (dry), then
`--apply`, then `check-stack-commands.sh --repair` as four more tool calls after
`preflight.sh` — the sweep's IN FLIGHT count read from its text and judged by hand, and
the `--apply` skipped whenever the dry run "looked fine" (a campaign reached 35 stranded
worktrees that way). The sweep is a GATE here: IN FLIGHT > 0 is committed work for an open
task that nobody holds, and re-dispatching that task starts it from scratch, so the run
stops until each such ref is adopted. The sweep's own summary block is the contract — its
absence from the output is a failure ("nothing measured"), never a zero. The repair is a
write to `harness.yaml`; when it changed something, the line says so, because that change
must be committed with the wave.

NOT HERE: `tk.sh memories`. That is content the loop reads, not a gate it passes, and a
gate script that also prints an index would put it in the orchestrator's context twice.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .check_project import EXIT_UPGRADE
from .resolve import HARNESS, REPO
from .steps import (
    DEFAULT_TIMEOUT,
    FAIL,
    INFO,
    OK,
    SKIP,
    UPGR,
    Raw,
    Result,
    execute,
    failure_detail,
    tail,
)

CHECKS = HARNESS / "checks"
SWARM = HARNESS / "swarm"
TK = HARNESS / "tracker" / "tk.sh"

#: Advisory lines a passing check still prints on stderr and the orchestrator must see:
#: a bound port (check-ports is advisory without --strict) and the config check's WARN
#: bullets. Surfaced on the step's line rather than lost with the rest of stderr.
SURFACE = re.compile(r"^(BOUND:|UPGRADE:|\s+- )")
#: How many dirty paths a failing `git status` names before summarising.
DIRTY_SHOWN = 12
#: The sweep's summary lines this judges by. Their absence is "the sweep did not report".
ORPHAN_BLOCK = re.compile(r"^\s*ORPHANED REFS", re.M)
IN_FLIGHT = re.compile(r"^\s*IN FLIGHT \(task still open\)\s*:\s*(\d+)", re.M)
DIRTY_WT = re.compile(r"^\s*DIRTY, left alone\s*:\s*(\d+)", re.M)
DETACHED_WT = re.compile(r"^\s*DETACHED, left alone\s*:\s*(\d+)", re.M)
#: The in-flight refs the sweep lists under its IN FLIGHT block: `    <ref>  (<ids>)`.
FLIGHT_REF = re.compile(r"^\s{4}(harness-w\S+|worktree-agent-\S+)\s+\(", re.M)
#: The repair's own line when it wrote to harness.yaml.
REPAIRED = re.compile(r"^\d+ command\(s\) repaired in harness\.yaml", re.M)


@dataclass(frozen=True)
class Step:
    name: str
    argv: tuple[str, ...]
    #: Whether a failure here stops the write step. `df` is information, never a gate.
    gate: bool = True
    #: The step that changes state; skipped when an earlier gate failed.
    write: bool = False
    timeout: int = DEFAULT_TIMEOUT


GIT_STATUS = Step("git status --porcelain", ("git", "status", "--porcelain"))
CONFIG = Step("check-project-config.sh --strict", (str(CHECKS / "check-project-config.sh"), "--strict"))
SLOT = Step("tk.sh slot-check", (str(TK), "slot-check", "--json"))
AUTOSYNC_OFF = Step("tk.sh autosync off", (str(TK), "autosync", "off"), write=True)
PORTS = Step("check-ports.sh", (str(CHECKS / "check-ports.sh"),))
DISK = Step("df -h .", ("df", "-h", "."), gate=False)
PRUNE = Step("git worktree prune", ("git", "worktree", "prune"), write=True)
SWEEP = Step("worktree-sweep.sh", (str(SWARM / "worktree-sweep.sh"),))
SWEEP_APPLY = Step("worktree-sweep.sh --apply", (str(SWARM / "worktree-sweep.sh"), "--apply"), write=True)
STACK_COMMANDS = Step("check-stack-commands.sh --repair", (str(CHECKS / "check-stack-commands.sh"), "--repair"), write=True)
RECORD_SIZE = Step("check-record-size.sh", (str(CHECKS / "check-record-size.sh"),), gate=False)

#: The loop's order, kept as a sequence so a test can pin it. The first six are §0's
#: gates as 0.10.10 collapsed them; the rest are the lines the loop ran after them.
STEPS: tuple[Step, ...] = (
    GIT_STATUS, CONFIG, SLOT, AUTOSYNC_OFF, PORTS, DISK,
    PRUNE, SWEEP, SWEEP_APPLY, STACK_COMMANDS, RECORD_SIZE,
)


def _surfaced(raw: Raw) -> str:
    hits = [ln.strip()[:160] for ln in raw.stderr.splitlines() if SURFACE.match(ln)]
    return "\n".join(hits)


def judge(step: Step, raw: Raw) -> Result:
    """Turn a step's raw outcome into its verdict line. The rules per step are small
    and stated here rather than spread over the loop's prose."""
    if not step.gate:
        detail = tail(raw.stdout, 1) if raw.ran and raw.returncode == 0 else f"unavailable: {failure_detail(raw)}"
        return Result(step.name, INFO, detail)
    if not raw.ran:
        return Result(step.name, FAIL, failure_detail(raw), raw)

    if step is GIT_STATUS:
        if raw.returncode != 0:
            return Result(step.name, FAIL, failure_detail(raw), raw)
        dirty = [ln for ln in raw.stdout.splitlines() if ln.strip()]
        if not dirty:
            return Result(step.name, OK, "clean", raw)
        shown = "\n".join(dirty[:DIRTY_SHOWN])
        more = f"\n… {len(dirty) - DIRTY_SHOWN} more" if len(dirty) > DIRTY_SHOWN else ""
        return Result(
            step.name, FAIL,
            f"{len(dirty)} path(s) dirty — another session may be mid-edit here; a campaign "
            f"on a dirty tree loses work:\n{shown}{more}", raw,
        )

    if step is CONFIG and raw.returncode == EXIT_UPGRADE:
        why = _surfaced(raw) or tail(raw.stderr) or "the config predates the installed plugin"
        return Result(step.name, UPGR, f"{why}\nStop, in both modes: the owner runs /harness-setup, then the campaign starts §0 again.", raw)

    if step is SLOT and raw.returncode == 0:
        try:
            state = json.loads(raw.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return Result(step.name, FAIL, f"slot-check answered but not in JSON:\n{tail(raw.stdout)}", raw)
        if state.get("free"):
            return Result(step.name, OK, "free", raw)
        holder = state.get("holder") or "?"
        if state.get("stale"):
            hint = "STALE — a previous run died holding it; `tk.sh slot-release --force` releases it"
        else:
            hint = "alive — another run is mid-merge on this checkout; wait for it, or /halt it"
        return Result(step.name, FAIL, f"held by {holder}: {hint}", raw)

    if step is SWEEP and raw.returncode == 0:
        text = raw.stdout + "\n" + raw.stderr
        if not ORPHAN_BLOCK.search(text):
            return Result(step.name, FAIL, "the sweep did not print its ORPHANED REFS summary — nothing measured; read its output", raw)
        m = IN_FLIGHT.search(text)
        flight = int(m.group(1)) if m else 0
        if flight:
            refs = FLIGHT_REF.findall(text)
            shown = "\n".join(f"  {r}" for r in refs[:DIRTY_SHOWN]) or "  (see the sweep's IN FLIGHT block)"
            return Result(
                step.name, FAIL,
                f"{flight} IN FLIGHT ref(s) — committed work for OPEN tasks that no worktree holds. Adopt each "
                f"(merge it, or dispatch the task FROM it: resume-point.sh <id>) before that task is dispatched again:\n{shown}", raw,
            )
        dirty = DIRTY_WT.search(text)
        detached = DETACHED_WT.search(text)
        notes = [f"{n} {what} left alone" for n, what in ((dirty and dirty.group(1), "dirty"), (detached and detached.group(1), "detached")) if n and n != "0"]
        return Result(step.name, OK, "no in-flight refs" + (f"; {', '.join(notes)} — read them" if notes else ""), raw)

    if step is STACK_COMMANDS and raw.returncode == 0:
        m = REPAIRED.search(raw.stdout)
        if m:
            return Result(step.name, OK, f"{m.group(0)} — harness.yaml CHANGED; commit it with the wave", raw)
        return Result(step.name, OK, tail(raw.stdout, 1) or "every declared command works", raw)

    if raw.returncode != 0:
        return Result(step.name, FAIL, failure_detail(raw), raw)
    detail = tail(raw.stdout, 1) or "ok"
    extra = _surfaced(raw)
    return Result(step.name, OK, f"{detail}\n{extra}" if extra else detail, raw)


def preflight(runner=None, cwd: str | None = None) -> list[Result]:
    """Run §0's gates in the loop's order. The write waits for the gates before it."""
    results: list[Result] = []
    for step in STEPS:
        if step.write and any(r.status in (FAIL, UPGR) for r in results):
            failed = ", ".join(r.name for r in results if r.status in (FAIL, UPGR))
            results.append(Result(step.name, SKIP, f"not run — {failed} failed; nothing was changed"))
            continue
        raw = execute(list(step.argv), cwd=cwd or str(REPO), timeout=step.timeout, runner=runner)
        results.append(judge(step, raw))
    return results


def exit_code(results: list[Result]) -> int:
    """3 when the config needs an upgrade — the stop that needs the owner — else 1 on
    any failure, else 0. UPGRADE wins over a plain failure because /harness-setup has
    to run before anything else does, and the campaign starts §0 again after it."""
    if any(r.status == UPGR for r in results):
        return EXIT_UPGRADE
    if any(r.status == FAIL for r in results):
        return 1
    return 0


def summary(results: list[Result]) -> str:
    n = {s: sum(1 for r in results if r.status == s) for s in (OK, FAIL, UPGR, SKIP)}
    code = exit_code(results)
    if code == EXIT_UPGRADE:
        verdict = "BLOCKED — the config predates the installed plugin; run /harness-setup (exit 3)"
    elif code:
        verdict = "BLOCKED — resolve the [FAIL] line(s) above, then run pre-flight again (exit 1)"
    else:
        verdict = "READY — proceed to §1; autosync is off until §5 (or /halt) restores it"
    return (
        f"\npre-flight: {n[OK]} ok, {n[FAIL]} failed, {n[UPGR]} upgrade, {n[SKIP]} skipped. {verdict}"
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .steps import render

    ap = argparse.ArgumentParser(
        prog="preflight.sh",
        description=(
            "campaign-loop §0 and /swarm step 1 in one call: clean tree, reviewed config, free merge "
            "slot, autosync off, ports, disk, worktree prune, the sweep (in-flight refs are a stop), "
            "sweep --apply, stack-command repair, record sizes. Exit 0 ready, 3 config upgrade needed, 1 otherwise."
        ),
    )
    ap.parse_args(argv)
    results = preflight()
    print(render(results))
    print(summary(results))
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
