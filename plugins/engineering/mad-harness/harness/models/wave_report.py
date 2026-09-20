"""The wave's health signals, computed — `/swarm` step 10 and the 8b accretion check.

"REPORT THESE FOUR NUMBERS EVERY WAVE, AND SAY WHICH DIRECTION EACH MOVED. A signal you
do not write down is not a signal." — and nothing wrote them down. Signal ② was a shell
pipeline in a table cell (`git log --oneline -200 | grep -ciE '^[0-9a-f]+ (fix|revert)'`),
③ was `git show --stat` per task, and the 8b accretion check was a bash `for` loop pasted
into the prose with `${MEGAFILE:-1000}` hardcoded where `signals.megafile_lines` should
have been read. Five to eight calls at the orchestrator's context price, arithmetic done
by a model, and the most skippable step in the file.

Every input is on the wave manifest or in git:

    ①  first-pass PASS rate    tasks whose round-1 lenses all passed ÷ tasks judged
    ①b L4 dispatch rate        rounds where L4 fired ÷ rounds that touched a security path
    ①c megafiles that grew     files past signals.megafile_lines whose line count rose
    ②  escape rate             fix:/revert: share of the last 200 commits, vs the baseline
    ③  lines per criterion     changed lines of each merged branch ÷ its acceptance criteria
    ④  wave yield + conflicts  closed ÷ dispatched, and the conflict count

The interpretation — what a moving number MEANS — stays in `/swarm` step 10's table.
This prints numbers and direction against the previous wave; a null baseline is "first
measurement", never zero; a manifest with no `dispatched` falls back to `planned` and
SAYS so.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from . import wave_manifest as wm
from .resolve import REPO

ESCAPE = re.compile(r"^[0-9a-f]+ (fix|revert)\b", re.I | re.M)
CRITERION = re.compile(r"^\s*(?:[-*]|\d+[.)]|\[ \]|AC\d+)", re.M)


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=120)
    return proc.stdout if proc.returncode == 0 else ""


def first_pass(doc: dict) -> tuple[int, int]:
    judged = passed = 0
    for rounds in doc.get("lenses", {}).values():
        if not rounds:
            continue
        judged += 1
        r1 = rounds[0]
        if all(r1.get(k) == "PASS" for k in ("L1", "L2", "L3") ) and r1.get("L4") in (None, "PASS"):
            passed += 1
    return passed, judged


def l4_rate(doc: dict) -> tuple[int, int]:
    fired = touched = 0
    for rounds in doc.get("lenses", {}).values():
        for r in rounds:
            if r.get("touched_security_path"):
                touched += 1
                if r.get("l4_fired"):
                    fired += 1
    return fired, touched


def megafiles_grew(doc: dict, threshold: int | None, cwd: Path) -> list[tuple[str, int, int]]:
    base, head = doc.get("wave_base"), doc.get("head") or "HEAD"
    if not threshold or not base:
        return []
    out = []
    for path in _git(cwd, "diff", "--name-only", f"{base}..{head}").splitlines():
        path = path.strip()
        if not path:
            continue
        after = _git(cwd, "show", f"{head}:{path}").count("\n")
        if after <= threshold:
            continue
        before = _git(cwd, "show", f"{base}:{path}").count("\n")
        if after > before:
            out.append((path, before, after))
    return out


def escape_rate(cwd: Path) -> tuple[int, int]:
    log = _git(cwd, "log", "--oneline", "-200")
    lines = [ln for ln in log.splitlines() if ln.strip()]
    return len(ESCAPE.findall(log)), len(lines)


def lines_per_criterion(doc: dict, cwd: Path, acceptance_for) -> list[tuple[str, int, int | None]]:
    out = []
    for m in doc.get("merged", []):
        branch, task = m.get("branch"), m.get("task")
        if not branch:
            continue
        stat = _git(cwd, "show", "--stat", "--format=", f"{branch}")
        ins = sum(int(x) for x in re.findall(r"(\d+) insertion", stat))
        dele = sum(int(x) for x in re.findall(r"(\d+) deletion", stat))
        text = acceptance_for(task) or ""
        n = len(CRITERION.findall(text)) or (1 if text.strip() else 0)
        out.append((task, ins + dele, n or None))
    return out


def compute(doc: dict, cwd: Path, project, acceptance_for) -> dict:
    p, j = first_pass(doc)
    f, t = l4_rate(doc)
    dispatched = len(doc.get("dispatched") or {})
    fallback = dispatched == 0
    denom = dispatched or len(doc.get("planned") or [])
    threshold = project.megafile_lines() if project else None
    esc, total = escape_rate(cwd)
    baseline = (project.signals().get("baselines") or {}).get("escape_rate") if project else None
    return {
        "epic": doc.get("epic"), "wave": doc.get("wave"),
        "first_pass": {"passed": p, "judged": j, "rate": (p / j) if j else None},
        "l4": {"fired": f, "touched": t, "rate": (f / t) if t else None},
        "megafiles_grew": [{"path": a, "before": b, "after": c} for a, b, c in megafiles_grew(doc, threshold, cwd)],
        "megafile_lines": threshold,
        "escape": {"count": esc, "of": total, "rate": (esc / total) if total else None, "baseline": baseline},
        "lines_per_criterion": [{"task": a, "lines": b, "criteria": c, "per": (b / c) if c else None} for a, b, c in lines_per_criterion(doc, cwd, acceptance_for)],
        "yield": {"closed": len(doc.get("closed") or []), "of": denom, "rate": (len(doc.get("closed") or []) / denom) if denom else None, "dispatched_unknown": fallback},
        "conflicts": len(doc.get("conflicts") or []),
    }


def _pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"


def _dir(now, prev) -> str:
    if now is None or prev is None:
        return ""
    if now > prev:
        return " ↑"
    if now < prev:
        return " ↓"
    return " ="


def render(sig: dict, prev: dict | None) -> str:
    pv = prev or {}
    fp, l4, esc, y = sig["first_pass"], sig["l4"], sig["escape"], sig["yield"]
    rows = [
        ("①  first-pass PASS rate", f"{_pct(fp['rate'])} ({fp['passed']}/{fp['judged']})" + _dir(fp["rate"], (pv.get("first_pass") or {}).get("rate"))),
        ("①b L4 dispatch rate", f"{_pct(l4['rate'])} ({l4['fired']}/{l4['touched']} rounds that touched a security path)" + _dir(l4["rate"], (pv.get("l4") or {}).get("rate"))),
        ("①c megafiles that grew", (", ".join(f"{m['path']} {m['before']}→{m['after']}" for m in sig["megafiles_grew"]) or "none") if sig["megafile_lines"] else "signals.megafile_lines not declared — not measured"),
        ("②  escape rate", f"{_pct(esc['rate'])} ({esc['count']} fix:/revert: of {esc['of']})" + (f" vs baseline {_pct(esc['baseline'])}" if esc["baseline"] is not None else " — no baseline recorded; this is the first measurement, record it in signals.baselines.escape_rate") + _dir(esc["rate"], (pv.get("escape") or {}).get("rate"))),
        ("③  lines per criterion", ", ".join(f"{r['task']} {r['lines']}/{r['criteria'] if r['criteria'] else 'n/a'}" + (" ⚠ >400" if r["per"] and r["per"] > 400 else "") + (" (no criteria!)" if not r["criteria"] else "") for r in sig["lines_per_criterion"]) or "no merged branch"),
        ("④  wave yield", f"{_pct(y['rate'])} ({y['closed']} closed of {y['of']}" + (" planned — `dispatched` not recorded, so this is yield over the PLAN)" if y["dispatched_unknown"] else " dispatched)") + _dir(y["rate"], (pv.get("yield") or {}).get("rate"))),
        ("④  merge conflicts", f"{sig['conflicts']}" + (" — a conflict is a step-3 miss by definition" if sig["conflicts"] else "")),
    ]
    width = max(len(k) for k, _ in rows)
    out = [f"wave-report {sig['epic']}-w{sig['wave']}" + (f" (direction vs w{pv.get('wave')})" if pv else " (first wave — no direction yet)")]
    out += [f"  {k:<{width}}  {v}" for k, v in rows]
    payload = {
        "first_pass_rate": round(100 * fp["rate"]) if fp["rate"] is not None else None,
        "l4_dispatch_rate": round(100 * l4["rate"]) if l4["rate"] is not None else None,
        "escape_rate": round(100 * esc["rate"]) if esc["rate"] is not None else None,
        "wave_yield": round(100 * y["rate"]) if y["rate"] is not None else None,
        "merge_conflicts": sig["conflicts"],
        "lines_per_ac": max((r["per"] for r in sig["lines_per_criterion"] if r["per"]), default=None),
        "megafiles_grew": len(sig["megafiles_grew"]),
    }
    out.append("  telemetry payload (for campaign-telemetry.sh record): " + json.dumps(payload))
    return "\n".join(out)


def _acceptance(task: str) -> str:
    try:
        import tracker

        t = tracker.task_store().show(task)
        return t.acceptance if t else ""
    except Exception:  # noqa: BLE001 — no tracker, no criteria count
        return ""


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .project import ProjectError, load

    ap = argparse.ArgumentParser(prog="wave-report.sh", description="/swarm step 10's signals and the 8b accretion check, computed from the wave manifest and git; direction against the previous wave.")
    ap.add_argument("wave", help="<epic>-w<n>, or a manifest path")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        project = load()
    except ProjectError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    path = wm.path_for(args.wave)
    try:
        doc = wm.load(path)
    except (OSError, ValueError) as exc:
        print(f"FAIL: {path}: {exc}", file=sys.stderr)
        return 2
    sig = compute(doc, REPO, project, _acceptance)
    prev = None
    try:
        earlier = [d for d in wm.all_waves(doc["epic"]) if d["wave"] < doc["wave"]]
        if earlier:
            prev = compute(earlier[-1], REPO, project, _acceptance)
    except ValueError:
        prev = None
    print(json.dumps(sig) if args.json else render(sig, prev))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
