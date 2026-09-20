"""One fresh headless orchestrator session per epic — the outer loop of `/campaign-auto`
as a script.

WHY A SCRIPT. An orchestrator's context grows with every epic it runs and never shrinks:
a measured session went 55k → 839k tokens over 225 requests without compacting, and every
request re-reads everything before it. An epic leaves ~300k behind; the next epic's ~110
requests would carry that for ~33M tokens — about the whole orchestrator cost of the
measured campaign — and the next epic is loaded from the tracker anyway. `/campaign` ends
its invocation at the boundary and asks for `/clear`; `/campaign-auto` cannot end its own
session, so this script ends it for it: each epic is a fresh `campaign-orchestrator`
dispatch, sandboxed, with a ceiling and a cost record, and the tracker is the only state
that crosses the boundary.

MEASURED before it was written (0.10.14): from inside its sandbox the orchestrator ran
pre-flight, tracker reads and writes, a nested worker dispatch, the merge, the gate and a
push, with zero denials — once the dispatcher was excluded from the sandbox (the keychain
is not reachable inside it) and the network policy reached the CLI.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from .resolve import HARNESS, REPO

DISPATCH = HARNESS / "models" / "dispatch.sh"
PREFLIGHT = HARNESS / "swarm" / "preflight.sh"
SIGNALS = HARNESS / "swarm" / "campaign-signals.sh"
TK = HARNESS / "tracker" / "tk.sh"
RUN_DIR = REPO / ".harness" / "run" / "campaign"

#: dispatch.sh exit codes this loop reacts to.
EXIT_OK, EXIT_NOT_OK, EXIT_REFUSED, EXIT_BUDGET = 0, 1, 2, 3
#: This script's own: the queue was worked (0), a gate stopped it (1), the account's usage
#: window closed (5) — the same code the A/B rig uses, so a wrapper can tell them apart.
STOP_USAGE = 5

Runner = Callable[..., subprocess.CompletedProcess]


def _run(argv: list[str], *, cwd: Path, runner: Runner | None = None, timeout: int | None = None) -> subprocess.CompletedProcess:
    run = runner or subprocess.run
    return run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)


EPIC_QUEUE = HARNESS / "swarm" / "epic-queue.sh"


def open_epics(*, runner: Runner | None = None, cwd: Path = REPO) -> list[dict[str, Any]]:
    """The runnable epics, in the loop's order — §1 and §2 as `epic-queue.sh` computes
    them: P0→P3 then oldest, every gated, parked or leased-elsewhere epic excluded with its
    reason, each with its triage state. This used to be `list --type epic --status open`
    and ASSUMED `status=blocked` covered the gate exclusion; 0.10.19 found a park that set
    no status, and this loop would have re-dispatched that epic forever."""
    out = _run([str(EPIC_QUEUE), "--json"], cwd=cwd, runner=runner)
    if out.returncode == 2:
        raise RuntimeError(f"epic-queue.sh failed: {(out.stderr or out.stdout).strip()[:300]}")
    try:
        doc = json.loads(out.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"epic-queue.sh did not answer in JSON: {exc}") from exc
    for n in doc.get("notes") or []:
        print(f"campaign: note — {n}", file=sys.stderr)
    for e in doc.get("epics") or []:
        if e.get("excluded"):
            print(f"campaign: {e['id']} excluded — {e['excluded']}", file=sys.stderr)
    return [{"id": e["id"], "title": e.get("title", ""), "triage": e.get("triage"), "ready": e.get("ready")} for e in (doc.get("epics") or []) if not e.get("excluded")]


def prompt_for(epic: dict[str, Any]) -> str:
    triage = f" It triaged {epic['triage']} with {epic.get('ready', '?')} task(s) dispatchable on entry." if epic.get("triage") else ""
    return (
        f"Run the campaign loop, MODE=auto, for epic `{epic['id']}` — {epic.get('title', '')} — and no other.\n"
        f"§0 pre-flight, §1 (the queue) and §2 (triage) have been done by the script that started you, and\n"
        f"the epic's lease is held for you.{triage}\n"
        f"Do §3 through §6 for this epic only — §3 is one call, `plan-epic.sh {epic['id']} --mode auto"
        + (f" --triage {epic['triage']}" if epic.get("triage") else "") + "` — then stop with the return contract,\n"
        "its first line the outcome: closed, parked or stopped. If the epic parks, say so and stop.\n"
    )


def last_terminal(cwd: Path = REPO) -> str:
    """The `terminal` of the most recent orchestrator dispatch, so a closed usage window
    stops the loop instead of being retried epic after epic."""
    path = cwd / ".harness" / "run" / "events" / "harness.dispatch.jsonl"
    try:
        lines = path.read_text().strip().splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            payload = json.loads(line).get("payload", {})
        except json.JSONDecodeError:
            continue
        if payload.get("agent") == "campaign-orchestrator":
            return str(payload.get("terminal") or "")
    return ""


def outcome_of(digest: str) -> str:
    """`closed`, `parked` or `stopped`, from the orchestrator's return contract — the
    outcome line the loop tells it to put first. Unknown reads as `stopped`: a session
    that said nothing recognisable did not close the epic."""
    lines = [ln.strip().lower() for ln in (digest or "").splitlines() if ln.strip()]
    # THE FIRST LINE, as the contract says. Searching the whole digest read "Tasks
    # closed: 0" in a session that began "**stopped**" as a closed epic (measured), and
    # campaign-signals then filed a stop as a catastrophically bad completion.
    words = ("parked", "closed", "stopped")
    if lines:
        for word in words:
            if word in lines[0]:
                return word
    for word in words:
        if any(word in ln for ln in lines):
            return word
    return "stopped"


def run_epic(epic: dict[str, Any], *, runner: Runner | None = None, cwd: Path = REPO, timeout: int = 4 * 3600) -> dict[str, Any]:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    prompt = RUN_DIR / f"{epic['id']}-prompt.md"
    prompt.write_text(prompt_for(epic))
    out_file = RUN_DIR / f"{epic['id']}-result.md"
    started = time.monotonic()
    # THE LEASE, HELD FOR THE EPIC'S WHOLE SESSION and released in a `finally`. §1 told the
    # orchestrator to acquire it and §5 to release it; nothing in code did either, and a
    # session cut off at its ceiling released nothing. A lease another machine holds is
    # already excluded by the queue; a lease this machine holds from a stopped run is
    # re-acquired here (the remote refuses only a ref that would not fast-forward).
    lease = _run([str(TK), "lease", "acquire", str(epic["id"])], cwd=cwd, runner=runner)
    if lease.returncode != 0:
        return {
            "epic": epic["id"], "exit": EXIT_REFUSED, "terminal": "lease", "seconds": 0,
            "digest": f"lease for {epic['id']} not acquired — {(lease.stderr or lease.stdout).strip()[:200]}",
            "stderr_tail": "", "result": None, "outcome": "stopped",
        }
    try:
        proc = _run(
            [str(DISPATCH), "campaign-orchestrator", "--prompt-file", str(prompt), "--task", str(epic["id"]),
             "--digest", "12", "--out", str(out_file)],
            cwd=cwd, runner=runner, timeout=timeout,
        )
    finally:
        released = _run([str(TK), "lease", "release", str(epic["id"])], cwd=cwd, runner=runner)
        if released.returncode != 0:
            print(f"campaign: lease for {epic['id']} not released — it expires on its TTL", file=sys.stderr)
    digest = (proc.stdout or "").strip()
    return {
        "epic": epic["id"],
        "exit": proc.returncode,
        "terminal": last_terminal(cwd),
        "seconds": int(time.monotonic() - started),
        "digest": digest,
        "stderr_tail": "\n".join((proc.stderr or "").strip().splitlines()[-4:]),
        "result": str(out_file) if out_file.exists() else None,
        "outcome": outcome_of(digest) if proc.returncode == EXIT_OK else ("stopped" if proc.returncode != EXIT_OK else "closed"),
    }


def main(argv: list[str] | None = None, *, runner: Runner | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="campaign.sh",
        description="/campaign-auto as one fresh headless orchestrator session per epic.",
    )
    ap.add_argument("--max-epics", type=int, default=None, help="stop after this many epics (default: the queue)")
    ap.add_argument("--epic", help="run this epic only")
    ap.add_argument("--skip-preflight", action="store_true", help="the caller already ran preflight.sh")
    args = ap.parse_args(argv)

    if not args.skip_preflight:
        pre = _run([str(PREFLIGHT)], cwd=REPO, runner=runner)
        print(pre.stdout.rstrip())
        if pre.returncode != 0:
            print(f"campaign: pre-flight exit {pre.returncode} — not starting", file=sys.stderr)
            return pre.returncode

    try:
        queue = open_epics(runner=runner)
    except RuntimeError as exc:
        print(f"campaign: {exc}", file=sys.stderr)
        return 1
    if args.epic:
        queue = [e for e in queue if e["id"] == args.epic] or [{"id": args.epic, "title": ""}]
    if args.max_epics is not None:
        queue = queue[: args.max_epics]
    if not queue:
        print("campaign: no open epics")
        return 0

    print(f"campaign: {len(queue)} epic(s) — one fresh session each")
    results = []
    try:
        for epic in queue:
            print(f"\n== {epic['id']} — {epic.get('title', '')}", flush=True)
            r = run_epic(epic, runner=runner)
            results.append(r)
            print(r["digest"])
            print(f"-- exit {r['exit']}, terminal {r['terminal'] or '?'}, outcome {r.get('outcome', '?')}, {r['seconds']}s", flush=True)
            # THE SIGNALS, RECORDED WITH THE OUTCOME the session reported. §6 had the
            # orchestrator compose an 11-key payload by hand and remember `--outcome
            # parked`; forgetting it filed a parked epic as a catastrophically bad closed
            # one (tracker/campaign.py). The script that owns the run records them.
            if r["exit"] != EXIT_REFUSED:
                sig = _run([str(SIGNALS), str(epic["id"]), "--outcome", r.get("outcome", "stopped"), "--record"], cwd=REPO, runner=runner)
                if sig.returncode != 0:
                    print(f"campaign: signals for {epic['id']} not recorded — {(sig.stderr or sig.stdout).strip()[:200]}", file=sys.stderr)
            if r["terminal"] == "usage_limit":
                print("campaign: the account's usage window closed — stopping; rerun when it reopens", file=sys.stderr)
                return STOP_USAGE
            if r["exit"] == EXIT_REFUSED:
                print("campaign: the dispatch was refused — a config problem, not an epic problem; stopping", file=sys.stderr)
                print(r["stderr_tail"], file=sys.stderr)
                return 1
    finally:
        # WHAT §0 DISABLED, §5 RESTORES — and a session that stopped before §5 restores
        # nothing. Measured: the first headless epic stopped at its ceiling after wave 1
        # and left `export.auto: false` behind, with a person to notice. This is the
        # /halt duty, done by the script that owns the run.
        restored = _run([str(TK), "autosync", "on"], cwd=REPO, runner=runner)
        if restored.returncode != 0:
            print("campaign: could not restore tracker autosync — run `tk.sh autosync on`", file=sys.stderr)
    closed = sum(1 for r in results if r["exit"] == EXIT_OK)
    print(f"\ncampaign: {len(results)} epic session(s), {closed} ended ok, {len(results) - closed} not ok. Results under {RUN_DIR}")
    return 0 if closed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
