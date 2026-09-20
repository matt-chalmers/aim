"""The verification gate for one task, as one call — `/swarm` step 7 and `/grind` §9.

WHAT THE ORCHESTRATOR DID BY HAND, per task, per round: build the brief (one call);
compute the L4 trigger from `git diff --name-only`, a grep of the diff body and the
task's `SURFACE:` line (two or three calls, with `brief.py` already holding all three
inputs); write three or four prompt files, enforcing by hand that L3's names no diff path;
dispatch them as background calls in one message; wait; read three or four verdict lines;
apply unanimity; route each FAIL by a fixed table; and on all-PASS type
`tk.sh note <id> "VERIFIED <sha>: L1 PASS · …"` — the one line `resume.py` machine-reads —
from four verdicts read by eye. Fifteen to twenty tool calls at the orchestrator's context
price (`steps.py` has the measurement), every one of them routing, with the judgement
entirely inside the lenses.

WHAT WAS WRONG BESIDES THE COST. The lenses ran in the primary checkout, at `main`,
BEFORE step 8 merged the branch — `/swarm` step 7 precedes step 8, and lenses are
dispatched without `--worker`. L1 read the diff by sha, which works; but L2 "re-runs the
suite" and L3 "reads the repository at HEAD", and both did so against a tree without the
change. Here the gate attaches to the branch's worktree (`--branch`, via the same
`prepare_worktree(resume=)` a resumed worker uses) and runs the suite and L2/L3 in it.
`/grind` has the change on the primary already and passes no `--branch`.

THE SUITE RUNS ONCE, HERE. `/swarm` step 7 said "only L2 executes tests; dispatch L2
first and pass its counts into L1's prompt — or run the scoped suite yourself once and
paste that output" and, three paragraphs earlier, "dispatch L1-L3 in parallel". The gate
runs the suite once in the branch's worktree, keeps the output at a path, hands that path
to L1 and L2, and dispatches all lenses at once. A red or hung suite before any lens runs
is COULD NOT JUDGE, not a FAIL: a lens over a red suite is the believe-the-report failure.

A MISSING VERDICT IS NEVER A PASS. A lens that hung, was denied a tool, was cut off at
its ceiling, or returned no `VERDICT:` line is NONE, and the gate exits 2 with nothing
written to the task. `broker.py` records a lens that could not read its brief and still
said PASS; the dispatcher's "any denial is not-ok" is what catches that here.

Exit 0 every lens PASS and the VERIFIED note written · 1 any lens FAIL (nothing written)
· 2 could not judge · 3 usage.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import fanout
from . import verdict as verdict_mod
from .resolve import HARNESS, REPO
from .steps import FAIL, INFO, OK, Result, execute, failure_detail, render, tail

BRIEF = HARNESS / "verify" / "brief.sh"
RUN = HARNESS / "verify" / "run.sh"
DISPATCH = HARNESS / "models" / "dispatch.sh"
TK = HARNESS / "tracker" / "tk.sh"

LENSES = {"L1": "verifier", "L2": "verifier-tests", "L3": "verifier-spec", "L4": "verifier-security"}
#: A lens that has produced nothing in this long is stuck.
LENS_TIMEOUT = 2400
SUITE_TIMEOUT = 1800
EXIT_PASS, EXIT_FAIL, EXIT_NO_JUDGE, EXIT_USAGE = 0, 1, 2, 3

BATCH = (
    "Gather evidence with the batch primitives: put every search into ONE `scan.sh` call and "
    "every slice you already know you want into ONE `peek.sh` call (the `evidence-gathering` "
    "skill has the forms). Never `git show <sha>`."
)


def _brief(task: str, sha: str, runner, cwd: str) -> tuple[dict | None, Result]:
    raw = execute([str(BRIEF), task, sha, "--json"], cwd=cwd, runner=runner)
    name = f"brief.sh {task} {sha[:12]}"
    if not raw.ran or raw.returncode != 0:
        return None, Result(name, FAIL, failure_detail(raw), raw)
    try:
        info = json.loads(raw.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None, Result(name, FAIL, f"brief.sh --json did not answer in JSON:\n{tail(raw.stdout)}", raw)
    l4 = info.get("l4") or {}
    why = f"L4 {'FIRES' if l4.get('fires') else 'not required'} — " + ("; ".join(l4.get("why") or []) or f"SURFACE: {l4.get('surface')}")
    return info, Result(name, OK, f"{info.get('files')} files → {info.get('brief')}\n{why}", raw)


def _suite(lane: str | None, cwd: str, root: Path, runner) -> tuple[Path | None, Result]:
    """The suite, once, where the change is. Its whole output goes to a file the lenses
    read by path; the line carries the last few lines."""
    argv = [str(RUN)] + (["--lane", lane] if lane else []) + ["test"]
    raw = execute(argv, cwd=cwd, timeout=SUITE_TIMEOUT, runner=runner)
    name = "run.sh test (once, where the change is)"
    out = root / "suite.txt"
    out.write_text((raw.stdout or "") + ("\n--- stderr ---\n" + raw.stderr if raw.stderr else ""))
    if not raw.ran:
        return None, Result(name, FAIL, f"could not judge — {failure_detail(raw)}", raw)
    if raw.returncode != 0:
        return None, Result(name, FAIL, f"could not judge — the suite is RED before any lens ran (exit {raw.returncode}); a lens over a red suite is the believe-the-report failure:\n{tail(raw.stdout) or tail(raw.stderr)}\nfull: {out}", raw)
    return out, Result(name, OK, f"{tail(raw.stdout, 1) or 'green'}\nfull: {out}", raw)


def prompts(task: str, sha: str, info: dict, suite: Path | None, branch: str | None, l4_reason: str) -> dict[str, str]:
    """One prompt per lens. L3's names the brief and NOTHING under the diff root — asserted."""
    brief = info["brief"]
    artefacts = info["artefacts"]
    where = f"The change is on branch `{branch}`; your working directory is its worktree." if branch else "The change is on the branch you are in, at HEAD."
    suite_line = f"The suite already ran once, where the change is; its whole output is at `{suite}`. Read it; do not re-run the whole suite." if suite else "The suite was not run by the gate."
    p = {
        "L1": f"""Judge task {task} for CORRECTNESS — commit {sha}. {where}
Read the brief at `{brief}` (it carries the task text, the acceptance criteria, the L4 trigger and the commit-hygiene facts) and the diff artefacts listed in `{artefacts}` — the per-file patches for the files you reason about.
{suite_line}
{BATCH}
Return `VERDICT: PASS` or `VERDICT: FAIL` on the first line, then every acceptance criterion located in the diff, then findings — each tagged blocking or filed, and a FAIL classified test-shaped or not.""",
        "L2": f"""Judge the TESTS of task {task} — commit {sha}. {where}
Read the brief at `{brief}` and the diff artefacts listed in `{artefacts}`.
{suite_line} Run targeted tests and the 3-of-N mutation spot-check against the resources `.swarm-env` names for this worktree — never a fixed name.
{BATCH}
Are the new tests adversarial or decorative — do they pin behaviour a plausible wrong implementation would fail? Return `VERDICT: PASS` or `VERDICT: FAIL` on the first line, then findings tagged blocking or filed.""",
        "L3": f"""Judge task {task} against the SPEC, the DOCS and its BLAST RADIUS. {where}
Read the brief at `{brief}` — the task text, its acceptance criteria and the measurements — and then the repository as it now stands. You are deliberately NOT given the diff or the worker's report: you are judging what the repository now claims, not how it changed, and that independence is what makes your findings worth having alongside L1's.
{BATCH}
Check callers of anything the task changed, other apps' assumptions, unregenerated types, the feature doc, the decision records, the corpus index, and the mechanical invariants. Return `VERDICT: PASS` or `VERDICT: FAIL` on the first line, then findings — each tagged blocking (caused by this change) or filed (pre-existing).""",
        "L4": f"""Judge task {task} for SECURITY and PRIVACY — commit {sha}. {where}
The trigger fired: {l4_reason}
Read the brief at `{brief}` and the diff artefacts listed in `{artefacts}`. Ask what the wrong person can now reach: authorization, tenant isolation, data exposure in responses and emails, auth and session handling, injection, secrets, and the declared security invariants.
{BATCH}
Return `VERDICT: PASS`, `VERDICT: PASS (no security surface)` or `VERDICT: FAIL` on the first line, then findings tagged blocking or filed with severity.""",
    }
    diff_root = str(Path(artefacts).parent)
    assert diff_root not in p["L3"] and "artefacts" not in p["L3"].lower(), "L3's prompt must name no diff path"
    return p


def route(verdicts: dict[str, verdict_mod.Verdict], branch: str | None) -> str:
    """The fixed table from `/swarm` step 7, as a recommendation. The decision — remediate,
    split on a third round, raise the priority — stays with the orchestrator."""
    lines = []
    resume = f" --resume {branch}" if branch else ""
    for lens, v in verdicts.items():
        if v.status != verdict_mod.FAIL:
            continue
        gov = "" if v.blocking or lens in ("L1", "L2") else "  (no finding tagged blocking — the governor says only blocking can FAIL; read it)"
        if lens == "L2" or (lens == "L1" and v.test_shaped):
            lines.append(f"{lens} FAIL → quality-engineer, after step 8 merges the branch, in its own worktree{gov}")
        elif lens in ("L1", "L3"):
            lines.append(f"{lens} FAIL → fullstack-engineer{resume} with the finding list{gov}")
        elif lens == "L4":
            lines.append(f"L4 FAIL → fullstack-engineer{resume}, and raise the task's priority to match the severity{gov}")
    return "\n".join(lines)


def run(
    task: str,
    sha: str,
    *,
    branch: str | None = None,
    lane: str | None = None,
    worker: int | None = None,
    l4: str = "auto",
    suite: str = "gate",
    wave: str | None = None,
    timeout: int = LENS_TIMEOUT,
    dry_run: bool = False,
    runner=None,
    cwd: str | None = None,
    worktree_for=None,
    dispatch_jobs=None,
    read_result=None,
) -> tuple[str, int, dict]:
    """The whole gate. Returns (report, exit status, facts for the manifest)."""
    at = cwd or str(REPO)
    results: list[Result] = []
    facts: dict = {"sha": sha, "round": None, "l4_fired": False, "touched_security_path": False, "verified": False}

    # ① brief + L4 trigger
    info, r = _brief(task, sha, runner, at)
    results.append(r)
    if info is None:
        return _report(task, results, None, None, EXIT_NO_JUDGE), EXIT_NO_JUDGE, facts
    root = Path(info["root"])
    root.mkdir(parents=True, exist_ok=True)
    l4info = info.get("l4") or {}
    fires = bool(l4info.get("fires")) or l4 == "always"
    facts.update(l4_fired=fires, touched_security_path=bool(l4info.get("touched_security_path")), sha=info.get("commit", sha))
    lenses = ["L1", "L2", "L3"] + (["L4"] if fires else [])

    # ② where the change is
    where = at
    if branch:
        try:
            wt = (worktree_for or _default_worktree)(branch, lane, worker, task)
        except Exception as exc:  # noqa: BLE001 — a worktree that cannot be had is a stop, named
            results.append(Result(f"worktree for {branch}", FAIL, f"could not judge — {exc}"))
            return _report(task, results, None, None, EXIT_NO_JUDGE), EXIT_NO_JUDGE, facts
        where = str(wt)
        results.append(Result(f"worktree for {branch}", OK, where))

    # ③ the suite, once
    suite_path: Path | None = None
    if suite == "gate":
        suite_path, r = _suite(lane, where, root, runner)
        results.append(r)
        if suite_path is None:
            return _report(task, results, None, None, EXIT_NO_JUDGE), EXIT_NO_JUDGE, facts
    else:
        results.append(Result("run.sh test", INFO, f"skipped (--suite {suite})"))

    # ④ prompts
    pr = prompts(task, info.get("commit", sha), info, suite_path, branch, "; ".join(l4info.get("why") or []) or "--l4 always")
    pdir = root / "prompts"
    pdir.mkdir(exist_ok=True)
    for lens in lenses:
        (pdir / f"{lens.lower()}.md").write_text(pr[lens] + "\n")
    results.append(Result("prompts", OK, f"{', '.join(lenses)} under {pdir} — L3's names no diff path"))

    if dry_run:
        results.append(Result("dispatch", INFO, "skipped (--dry-run)"))
        return _report(task, results, None, None, EXIT_PASS), EXIT_PASS, facts

    # ⑤ dispatch, all at once
    jobs = []
    for lens in lenses:
        argv = [str(DISPATCH), LENSES[lens], "--prompt-file", str(pdir / f"{lens.lower()}.md"), "--task", task,
                "--out", str(root / f"{lens.lower()}.result.md"), "--digest", "0"]
        if branch and lens in ("L2", "L3"):
            argv += ["--cwd", where]
        jobs.append(fanout.Job(name=lens, argv=tuple(argv), cwd=at, timeout=timeout, task=task))
    outcomes = (dispatch_jobs or fanout.run_jobs)(jobs, len(jobs))

    # ⑥ verdicts — a lens that did not answer cleanly is NONE, never PASS
    verdicts: dict[str, verdict_mod.Verdict] = {}
    for job, out in zip(jobs, outcomes):
        path = root / f"{job.name.lower()}.result.md"
        text = (read_result or _read)(path)
        v = verdict_mod.parse(text)
        if out.status != fanout.OK:
            why = "hung" if out.status == fanout.HUNG else f"dispatch exit {out.rc} — a denial, a refusal or a budget kill; the work did not happen whatever the text claims"
            verdicts[job.name] = verdict_mod.Verdict(verdict_mod.NONE, why)
            results.append(Result(f"{job.name} {LENSES[job.name]}", FAIL, f"NONE — {why}  ({out.seconds}s)\nfull: {out.full_path or path}"))
            continue
        verdicts[job.name] = v
        if v.status == verdict_mod.NONE:
            results.append(Result(f"{job.name} {LENSES[job.name]}", FAIL, f"NONE — no `VERDICT:` line in its result ({out.seconds}s)\nfull: {path}"))
        else:
            detail = f"{v.status}{(' ' + v.qualifier) if v.qualifier else ''} — {v.blocking} blocking, {v.filed} filed  ({out.seconds}s)\nfull: {path}"
            results.append(Result(f"{job.name} {LENSES[job.name]}", OK if v.status == verdict_mod.PASS else FAIL, detail))

    # ⑦ unanimity
    statuses = {k: v.status for k, v in verdicts.items()}
    facts["lenses"] = statuses
    if any(s == verdict_mod.FAIL for s in statuses.values()):
        code = EXIT_FAIL
    elif all(s == verdict_mod.PASS for s in statuses.values()):
        code = EXIT_PASS
    else:
        code = EXIT_NO_JUDGE

    # ⑧ the note, only on unanimity
    note = None
    if code == EXIT_PASS:
        from .resume import verified_note

        note = verified_note(info.get("commit", sha), {k: "PASS" for k in lenses})
        raw = execute([str(TK), "note", task, note], cwd=at, runner=runner)
        if not raw.ran or raw.returncode != 0:
            results.append(Result(f"tk.sh note {task}", FAIL, f"every lens passed but the VERIFIED note was not written — write it by hand:\n{TK} note {task} \"{note}\"\n{failure_detail(raw)}", raw))
            code = EXIT_NO_JUDGE
        else:
            facts["verified"] = True
            results.append(Result(f"tk.sh note {task}", OK, note, raw))

    # ⑨ the manifest
    if wave:
        try:
            from . import wave_manifest

            path = wave_manifest.path_for(wave)
            doc = wave_manifest.load(path)
            facts["round"] = len(doc.get("lenses", {}).get(task, [])) + 1
            wave_manifest.append(path, "lenses", {**facts, **{k: statuses.get(k) for k in ("L1", "L2", "L3", "L4")}}, task=task)
            results.append(Result("manifest", INFO, f"{path.name}: round {facts['round']} recorded"))
        except (OSError, ValueError) as exc:
            results.append(Result("manifest", INFO, f"not recorded — {exc}"))
    else:
        results.append(Result("manifest", INFO, "none (no --wave)"))

    return _report(task, results, verdicts, route(verdicts, branch), code), code, facts


def _default_worktree(branch: str, lane: str | None, worker: int | None, task: str) -> Path:
    from .dispatch import prepare_worktree

    return prepare_worktree("verifier-tests", worker or 1, lane, task, resume=branch)


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def _report(task: str, results: list[Result], verdicts, routing: str | None, code: int) -> str:
    out = [f"lens-gate {task}", render(results)]
    if code == EXIT_PASS and verdicts:
        out.append("\nVERDICT: PASS — every lens passed; VERIFIED recorded on the task. Merge it in step 8.")
    elif code == EXIT_PASS:
        out.append("\nDRY RUN — nothing dispatched.")
    elif code == EXIT_FAIL:
        out.append("\nVERDICT: FAIL — nothing written to the task. Route:\n" + (routing or "  (see the lens lines)"))
        out.append("The decision is yours: remediate as routed, or — on a THIRD round — split the task along the seams the rounds revealed.")
    else:
        out.append("\nCOULD NOT JUDGE — nothing written to the task. Resolve the [FAIL] line(s) above and run the gate again; a missing verdict is never a PASS.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="lens-gate.sh",
        description="/swarm step 7 and /grind §9 as one call: brief + L4 trigger, the suite once where the change is, three or four lenses at once, unanimity, the VERIFIED note. Exit 0 PASS, 1 FAIL, 2 could not judge.",
    )
    ap.add_argument("task")
    ap.add_argument("sha", help="the commit under judgement")
    ap.add_argument("--branch", help="the worker branch holding the commit (a /swarm wave); omit when the change is on the primary (/grind)")
    ap.add_argument("--lane", default=None)
    ap.add_argument("--worker", type=int, default=None, help="the worker number whose worktree holds the branch (for its .swarm-env resources)")
    ap.add_argument("--l4", choices=["auto", "always"], default="auto", help="dispatch verifier-security on the computed trigger (auto) or regardless")
    ap.add_argument("--suite", choices=["gate", "skip"], default="gate", help="run the suite once before the lenses (gate) or not at all")
    ap.add_argument("--wave", help="record this round on the wave manifest (<epic>-w<n>)")
    ap.add_argument("--timeout", type=int, default=LENS_TIMEOUT, help="seconds per lens before it is HUNG")
    ap.add_argument("--dry-run", action="store_true", help="brief, suite and prompts; dispatch nothing")
    ap.add_argument("--json", action="store_true", help="print the facts as JSON on the last line")
    args = ap.parse_args(argv)
    if not args.task.strip() or not args.sha.strip():
        ap.error("a task id and a commit are required")
    text, code, facts = run(
        args.task, args.sha, branch=args.branch, lane=args.lane, worker=args.worker, l4=args.l4,
        suite=args.suite, wave=args.wave, timeout=args.timeout, dry_run=args.dry_run,
    )
    print(text)
    if args.json:
        print(json.dumps(facts))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
