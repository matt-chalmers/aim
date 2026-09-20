"""Read an A/B series back — per arm, per dispatch, with the spread shown, not hidden.

`ab.sh` runs the same seeded epic N times with a lever off and N with it on, in a fresh
repository per run, and every dispatch event carries `experiment = lever:arm:run`. This
reads every run's events under one root and answers, for each metric a lever can move:
what did the off arm do, what did the on arm do, and do the two spreads even separate?

Medians and interquartile ranges rather than means, because the cost analysis that
motivated this measured up to 30x token variance on identical work — one wild run must
not be allowed to be the result. A delta whose IQRs overlap is reported as such, in
words, so it cannot be read as a finding.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

#: metric key -> (label, unit, "lower is better")
METRICS: tuple[tuple[str, str, str, bool], ...] = (
    ("cost_usd", "cost / dispatch", "$", True),
    ("turns", "turns", "", True),
    ("cache_hit_pct", "cache hit", "%", False),
    ("cache_write_pct", "cache write", "%", True),
    ("cache_creation_tokens", "cache written", "tok", True),
    ("output_tokens", "output", "tok", True),
    ("tool_result_chars", "tool results", "chars", True),
    ("carried_result_tokens", "results carried", "tok", True),
    ("cache_breaks", "cache breaks", "", True),
)


def load(root: Path, lever: str) -> dict[str, list[dict[str, Any]]]:
    """arm -> the dispatch events of every run of that arm under `root`."""
    by_arm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for events in sorted(root.glob(f"{lever}-*/*/.harness/run/events/harness.dispatch.jsonl")):
        for line in events.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = row.get("payload", row)
            label = str(payload.get("experiment") or "")
            if not label.startswith(f"{lever}:"):
                continue
            _, arm, run = label.split(":", 2)
            sha_file = events.parents[4] / ".ab-sha"
            sha = sha_file.read_text().strip() if sha_file.is_file() else "?"
            payload = dict(payload, _arm=arm, _run=run, _sha=sha)
            by_arm[arm].append(payload)
    return dict(by_arm)


#: Writers, whose cost a lever is about; everything else in a run is the judging of it.
WRITERS = ("fullstack-engineer", "quality-engineer")
#: The one agent whose turns ARE the orchestration cost — present only in an
#: `ab.sh --orchestrated` run, where it drove the whole epic through `campaign.sh`.
ORCHESTRATOR = "campaign-orchestrator"


def load_outcomes(root: Path, lever: str) -> dict[str, dict[str, dict[str, Any]]]:
    """arm -> run -> the tracker's end state (`outcome.json`, written by an orchestrated run)."""
    out: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for path in sorted(root.glob(f"{lever}-*/outcome.json")):
        run_dir = path.parent.name
        arm, run = run_dir[len(lever) + 1 :].rsplit("-", 1)
        try:
            out[arm][run] = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
    return dict(out)
LENS_LABEL = {"verifier": "L1", "verifier-tests": "L2", "verifier-spec": "L3", "verifier-security": "L4"}


def load_verdicts(root: Path, lever: str) -> dict[str, list[tuple[str, str, str]]]:
    """arm -> (task, lens agent, PASS|FAIL|NONE) for every judged task, from the
    `lens-verdicts.txt` lens-wave.sh keeps beside a run's events."""
    out: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for path in sorted(root.glob(f"{lever}-*/*/.harness/run/lens-verdicts.txt")):
        run_dir = path.parents[3].name  # <lever>-<arm>-<run>
        arm = run_dir[len(lever) + 1 :].rsplit("-", 1)[0]
        for line in path.read_text().splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                out[arm].append((parts[0], parts[1], parts[2]))
    return dict(out)


def pass_rates(rows: list[tuple[str, str, str]]) -> dict[str, tuple[int, int, int]]:
    """lens -> (pass, fail, no verdict)."""
    by: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for _, agent, v in rows:
        i = 0 if v == "PASS" else 1 if v == "FAIL" else 2
        by[LENS_LABEL.get(agent, agent)][i] += 1
    return {k: (v[0], v[1], v[2]) for k, v in by.items()}


def _quartiles(xs: list[float]) -> tuple[float, float, float]:
    xs = sorted(xs)
    if len(xs) == 1:
        return xs[0], xs[0], xs[0]
    q = statistics.quantiles(xs, n=4, method="inclusive")
    return q[0], statistics.median(xs), q[2]


def summarise(by_arm: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """arm -> {n_dispatches, n_runs, kills, cost_per_run: (q1, med, q3), <metric>: (q1, med, q3)}."""
    out: dict[str, dict[str, Any]] = {}
    for arm, rows in by_arm.items():
        s: dict[str, Any] = {
            "n_dispatches": len(rows),
            "n_runs": len({r["_run"] for r in rows}),
            "shas": sorted({r.get("_sha", "?") for r in rows}),
            "kills": sum(1 for r in rows if r.get("terminal") == "budget"),
            "not_ok": sum(1 for r in rows if not r.get("ok")),
            # A dispatch the timeout killed has turns but no cost (None): the arm's cost
            # is a FLOOR then, and the report says so rather than summing a zero.
            "timeouts": sum(1 for r in rows if r.get("terminal") == "timeout"),
        }
        per_run: dict[str, float] = defaultdict(float)
        writers_per_run: dict[str, float] = defaultdict(float)
        for r in rows:
            per_run[r["_run"]] += float(r.get("cost_usd") or 0)
            if r.get("agent") in WRITERS:
                writers_per_run[r["_run"]] += float(r.get("cost_usd") or 0)
        s["cost_per_run"] = _quartiles(list(per_run.values()))
        # With --lenses a run's cost includes the judging; the writers' share is what the
        # earlier series measured, so both are kept.
        s["writer_cost_per_run"] = _quartiles(list(writers_per_run.values())) if writers_per_run else None
        s["judged"] = any(r.get("agent") in LENS_LABEL for r in rows)
        # THE ORCHESTRATOR'S OWN PRICE, kept apart from the work it dispatched. Its turns
        # are the count the prose-to-code series claims to cut; its cost is those turns at
        # its context's price; the children are what it spent them on.
        orch = [r for r in rows if r.get("agent") == ORCHESTRATOR]
        if orch:
            s["orch_turns"] = _quartiles([float(r.get("turns") or 0) for r in orch])
            s["orch_cost"] = _quartiles([float(r.get("cost_usd") or 0) for r in orch])
            s["orch_input"] = _quartiles([float((r.get("input_tokens") or 0) + (r.get("cache_read_tokens") or 0) + (r.get("cache_creation_tokens") or 0)) for r in orch])
            s["orch_minutes"] = _quartiles([float(r.get("duration_ms") or 0) / 60000 for r in orch])
            s["orch_terminals"] = sorted({str(r.get("terminal")) for r in orch})
            kids: dict[str, int] = defaultdict(int)
            lens: dict[str, int] = defaultdict(int)
            for r in rows:
                if r.get("agent") == ORCHESTRATOR:
                    continue
                kids[r["_run"]] += 1
                if r.get("agent") in LENS_LABEL:
                    lens[r["_run"]] += 1
            s["children_per_run"] = _quartiles([float(kids[k]) for k in per_run])
            s["lens_per_run"] = _quartiles([float(lens[k]) for k in per_run])
        for key, *_ in METRICS:
            xs = [float(r[key]) for r in rows if r.get(key) is not None]
            s[key] = _quartiles(xs) if xs else None
        out[arm] = s
    return out


def verdict(off: tuple[float, float, float] | None, on: tuple[float, float, float] | None, lower_better: bool) -> str:
    if not off or not on:
        return "no data"
    better = (on[1] < off[1]) if lower_better else (on[1] > off[1])
    if off[1] == on[1]:
        return "no change"
    delta = 100 * (on[1] - off[1]) / off[1] if off[1] else float("inf")
    separated = (on[2] < off[0]) or (on[0] > off[2])  # IQRs do not overlap
    word = "better" if better else "worse"
    return f"{delta:+.0f}% median, {word}" + (" — spreads separate" if separated else " — spreads OVERLAP, not a finding")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0].startswith("-"):
        print("usage: ab-report.sh <lever> [--root DIR]", file=sys.stderr)
        return 2
    lever = args[0]
    root = Path(args[args.index("--root") + 1]) if "--root" in args else Path.home() / "harness-wavelab-ab"
    by_arm = load(root, lever)
    if not by_arm:
        print(f"no events for lever {lever!r} under {root} — run ab.sh first")
        return 1
    s = summarise(by_arm)
    off, on = s.get("off"), s.get("on")
    verdicts = load_verdicts(root, lever)
    outcomes = load_outcomes(root, lever)
    print(f"A/B {lever} — root {root}")
    for arm in ("off", "on"):
        if arm not in s:
            continue
        a = s[arm]
        q1, med, q3 = a["cost_per_run"]
        code = ", ".join(a["shas"]) + ("  ← MIXED CODE across runs; do not read this arm as one sample" if len(a["shas"]) > 1 else "")
        print(f"\n[{arm}]  {a['n_runs']} run(s), {a['n_dispatches']} dispatch(es), {a['kills']} budget kill(s), {a['not_ok']} not-ok, code {code}")
        floor = f"   — a FLOOR: {a['timeouts']} dispatch(es) killed at timeout, cost unknown" if a.get("timeouts") else ""
        print(f"  cost / run        ${med:.2f}   (IQR ${q1:.2f}–${q3:.2f}){'   — writers AND lenses' if a.get('judged') else ''}{floor}")
        if a.get("judged") and a.get("writer_cost_per_run"):
            wq1, wmed, wq3 = a["writer_cost_per_run"]
            print(f"  writers / run     ${wmed:.2f}   (IQR ${wq1:.2f}–${wq3:.2f})")
        if a.get("orch_turns"):
            t, c, i, m = a["orch_turns"], a["orch_cost"], a["orch_input"], a["orch_minutes"]
            print(f"  orchestrator      {t[1]:.0f} turns (IQR {t[0]:.0f}–{t[2]:.0f})   ${c[1]:.2f} (IQR ${c[0]:.2f}–${c[2]:.2f})   {i[1]:,.0f} input tok   {m[1]:.0f} min   ended: {', '.join(a['orch_terminals'])}" + ("   (a timeout's cost is unknown; its turns are real)" if "timeout" in a["orch_terminals"] else ""))
            k, ln = a["children_per_run"], a["lens_per_run"]
            print(f"  dispatched        {k[1]:.0f} agents / epic (IQR {k[0]:.0f}–{k[2]:.0f}), of which {ln[1]:.0f} lenses")
            for run, o in sorted((outcomes.get(arm) or {}).items()):
                print(f"  outcome run {run}     epic {o.get('epic_status')}, {o.get('closed')}/{o.get('tasks')} tasks closed" + (f", open: {', '.join(o['open'])}" if o.get("open") else ""))
        if arm in verdicts:
            rates = pass_rates(verdicts[arm])
            cells = "   ".join(f"{k} {p}/{p + f} pass" + (f" ({n} no verdict)" if n else "") for k, (p, f, n) in sorted(rates.items()))
            print(f"  first-pass lenses {cells}")
        for key, label, unit, _ in METRICS:
            v = a.get(key)
            if v:
                fmt = (lambda x: f"${x:.3f}") if unit == "$" else (lambda x: f"{x:,.0f}{unit if unit not in ('tok', 'chars') else ''}")
                print(f"  {label:<18}{fmt(v[1]):>12}   (IQR {fmt(v[0])}–{fmt(v[2])})")
    if off and on:
        print("\ndelta, on vs off (medians; a delta whose IQRs overlap is noise until more runs say otherwise):")
        print(f"  {'cost / run':<18}{verdict(off['cost_per_run'], on['cost_per_run'], True)}")
        if off.get("orch_turns") and on.get("orch_turns"):
            print(f"  {'orch turns':<18}{verdict(off['orch_turns'], on['orch_turns'], True)}")
            print(f"  {'orch cost':<18}{verdict(off['orch_cost'], on['orch_cost'], True)}")
            print(f"  {'orch input tok':<18}{verdict(off['orch_input'], on['orch_input'], True)}")
            print(f"  {'agents / epic':<18}{verdict(off['children_per_run'], on['children_per_run'], True)}")
        for key, label, _, lower in METRICS:
            print(f"  {label:<18}{verdict(off.get(key), on.get(key), lower)}")
        if on["kills"] != off["kills"]:
            print(f"  {'budget kills':<18}{off['kills']} → {on['kills']}")
        if "off" in verdicts and "on" in verdicts:
            ro, rn = pass_rates(verdicts["off"]), pass_rates(verdicts["on"])
            for lens in sorted(set(ro) | set(rn)):
                po, fo, _ = ro.get(lens, (0, 0, 0))
                pn, fn, _ = rn.get(lens, (0, 0, 0))
                so = f"{100 * po // max(1, po + fo)}%"
                sn = f"{100 * pn // max(1, pn + fn)}%"
                print(f"  {lens + ' first-pass':<18}{so} → {sn}   (a lever that costs less by doing less of the doctrine shows here, not above)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
