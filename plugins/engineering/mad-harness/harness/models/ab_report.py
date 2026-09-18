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
        }
        per_run: dict[str, float] = defaultdict(float)
        for r in rows:
            per_run[r["_run"]] += float(r.get("cost_usd") or 0)
        s["cost_per_run"] = _quartiles(list(per_run.values()))
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
    print(f"A/B {lever} — root {root}")
    for arm in ("off", "on"):
        if arm not in s:
            continue
        a = s[arm]
        q1, med, q3 = a["cost_per_run"]
        code = ", ".join(a["shas"]) + ("  ← MIXED CODE across runs; do not read this arm as one sample" if len(a["shas"]) > 1 else "")
        print(f"\n[{arm}]  {a['n_runs']} run(s), {a['n_dispatches']} dispatch(es), {a['kills']} budget kill(s), {a['not_ok']} not-ok, code {code}")
        print(f"  cost / run        ${med:.2f}   (IQR ${q1:.2f}–${q3:.2f})")
        for key, label, unit, _ in METRICS:
            v = a.get(key)
            if v:
                fmt = (lambda x: f"${x:.3f}") if unit == "$" else (lambda x: f"{x:,.0f}{unit if unit not in ('tok', 'chars') else ''}")
                print(f"  {label:<18}{fmt(v[1]):>12}   (IQR {fmt(v[0])}–{fmt(v[2])})")
    if off and on:
        print("\ndelta, on vs off (medians; a delta whose IQRs overlap is noise until more runs say otherwise):")
        print(f"  {'cost / run':<18}{verdict(off['cost_per_run'], on['cost_per_run'], True)}")
        for key, label, _, lower in METRICS:
            print(f"  {label:<18}{verdict(off.get(key), on.get(key), lower)}")
        if on["kills"] != off["kills"]:
            print(f"  {'budget kills':<18}{off['kills']} → {on['kills']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
