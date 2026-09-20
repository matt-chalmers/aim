"""The campaign's health signals for one epic, computed — `campaign-loop` §6 as a script.

"RECORD THE SIGNALS, DO NOT ONLY NARRATE THEM." §6 listed eight signals and had the
orchestrator compose an eleven-key JSON payload by hand for `campaign-telemetry.sh record`,
at its context price, from numbers it had computed by eye — and remember `--outcome
parked` when the epic did not close. `tracker/campaign.py` records what forgetting that
did: a parked epic filed as a catastrophically bad closed one, the trend line drawn
straight through. Every input is on disk now: the wave manifests (rounds, L4, merges,
conflicts, closes), the dispatch telemetry (every agent that ran, with its outcome), the
epic's notes (the `ADEQUACY:` and `AUDIT:` verdicts signal ①d exists to count), and git.

    ①  first_pass_rate       round-1 all-PASS ÷ tasks judged, over every wave
    ①b l4_dispatch_rate      rounds where L4 fired ÷ rounds touching a security path
    ①c megafiles_grew        files past signals.megafile_lines that grew, over the waves
    ①d analyst_gate_rate     of the gates (ADEQUACY, AUDIT) the epic should carry, how many it does
    ②  escape_rate           fix:/revert: share of the last 200 commits
    ③  lines_per_ac          the worst merged task's changed lines per acceptance criterion
    ④  wave_yield            closed ÷ dispatched over the waves;  merge_conflicts
    ⑤  dispatchable_on_entry the first wave's planned count (what the queue's triage saw)
    +  beads_closed, waves, mode, and the dispatch cost per tier

`campaign.py::CHECKS` carries the bands; `l4_dispatch_rate` and `analyst_gate_rate` had
none until now — two of the eight were recorded blind.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from . import wave_manifest as wm
from . import wave_report
from .resolve import REPO

ADEQUACY = re.compile(r"^\s*ADEQUACY:", re.M)
AUDIT = re.compile(r"^\s*AUDIT:\s*(PASS|FAIL)", re.M | re.I)


def gate_rate(notes: str) -> tuple[int, int, int]:
    """(gates present, gates expected, audit findings) — signal ①d and its finding rate."""
    expected = 2
    present = int(bool(ADEQUACY.search(notes or ""))) + int(bool(AUDIT.search(notes or "")))
    fails = len([m for m in AUDIT.finditer(notes or "") if m.group(1).upper() == "FAIL"])
    return present, expected, fails


def compute(epic: str, *, cwd: Path, project, notes: str, events: list[dict], acceptance_for, mode: str | None = None) -> dict:
    waves = wm.all_waves(epic)
    per_wave = [wave_report.compute(w, cwd, project, acceptance_for) for w in waves]
    passed = sum(s["first_pass"]["passed"] for s in per_wave)
    judged = sum(s["first_pass"]["judged"] for s in per_wave)
    l4f = sum(s["l4"]["fired"] for s in per_wave)
    l4t = sum(s["l4"]["touched"] for s in per_wave)
    grew = sorted({m["path"] for s in per_wave for m in s["megafiles_grew"]})
    closed = sum(s["yield"]["closed"] for s in per_wave)
    dispatched = sum(s["yield"]["of"] for s in per_wave)
    conflicts = sum(s["conflicts"] for s in per_wave)
    worst_lpa = max((r["per"] for s in per_wave for r in s["lines_per_criterion"] if r["per"]), default=None)
    esc = wave_report.escape_rate(cwd)
    present, expected, audit_fails = gate_rate(notes)
    mine = [e for e in events if e.get("task") == epic or (e.get("task") or "").startswith(epic + ".")]
    cost = {}
    for e in mine:
        tier = e.get("tier") or "?"
        c = cost.setdefault(tier, {"dispatches": 0, "cost_usd": 0.0, "not_ok": 0})
        c["dispatches"] += 1
        c["cost_usd"] = round(c["cost_usd"] + float(e.get("cost_usd") or 0), 4)
        c["not_ok"] += 0 if e.get("ok") else 1
    return {
        "epic": epic,
        "first_pass_rate": round(100 * passed / judged) if judged else None,
        "l4_dispatch_rate": round(100 * l4f / l4t) if l4t else None,
        "megafiles_grew": len(grew),
        "analyst_gate_rate": round(100 * present / expected),
        "audit_findings": audit_fails,
        "escape_rate": round(100 * esc[0] / esc[1]) if esc[1] else None,
        "lines_per_ac": round(worst_lpa) if worst_lpa else None,
        "wave_yield": round(100 * closed / dispatched) if dispatched else None,
        "merge_conflicts": conflicts,
        "dispatchable_on_entry": len(waves[0].get("planned") or []) if waves else 0,
        "beads_closed": closed,
        "waves": len(waves),
        "mode": mode,
        "cost_by_tier": cost,
        "_megafiles": grew,
    }


def payload(sig: dict) -> dict:
    keys = ("first_pass_rate", "l4_dispatch_rate", "megafiles_grew", "analyst_gate_rate", "audit_findings", "escape_rate",
            "lines_per_ac", "wave_yield", "merge_conflicts", "dispatchable_on_entry", "beads_closed", "waves", "mode")
    return {k: sig.get(k) for k in keys if sig.get(k) is not None}


def render(sig: dict) -> str:
    def pct(v):
        return "—" if v is None else f"{v}%"

    lines = [
        f"campaign-signals {sig['epic']}: {sig['waves']} wave(s), {sig['beads_closed']} closed",
        f"  ①  first-pass PASS rate     {pct(sig['first_pass_rate'])}",
        f"  ①b L4 dispatch rate         {pct(sig['l4_dispatch_rate'])}",
        f"  ①c megafiles that grew      {sig['megafiles_grew']}" + (f" ({', '.join(sig['_megafiles'][:4])})" if sig["_megafiles"] else ""),
        f"  ①d analyst gate rate        {sig['analyst_gate_rate']}% (ADEQUACY:/AUDIT: on the epic), {sig['audit_findings']} audit FAIL(s)",
        f"  ②  escape rate              {pct(sig['escape_rate'])}",
        f"  ③  lines per criterion      {sig['lines_per_ac'] if sig['lines_per_ac'] is not None else '—'} (worst merged task)",
        f"  ④  wave yield               {pct(sig['wave_yield'])}, {sig['merge_conflicts']} conflict(s)",
        f"  ⑤  dispatchable on entry    {sig['dispatchable_on_entry']}",
    ]
    if sig["cost_by_tier"]:
        lines.append("  cost: " + "; ".join(f"{t}: {c['dispatches']} dispatch(es) ${c['cost_usd']:.2f}, {c['not_ok']} not ok" for t, c in sorted(sig["cost_by_tier"].items())))
    lines.append("  payload: " + json.dumps(payload(sig)))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .project import ProjectError, load

    ap = argparse.ArgumentParser(prog="campaign-signals.sh", description="campaign-loop §6's health signals for one epic, computed from the wave manifests, the dispatch telemetry, the epic's notes and git; --record files them with the outcome.")
    ap.add_argument("epic")
    ap.add_argument("--outcome", choices=["closed", "parked", "stopped"], default="closed")
    ap.add_argument("--mode", default=None, help="interactive | auto")
    ap.add_argument("--record", action="store_true", help="write the payload as a campaign event with --outcome")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        project = load()
    except ProjectError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    import tracker

    notes = ""
    acceptance_for = lambda t: ""  # noqa: E731
    try:
        store = tracker.task_store()
        t = store.show(args.epic)
        notes = t.notes if t else ""

        def acceptance_for(tid):  # noqa: F811
            x = store.show(tid)
            return x.acceptance if x else ""
    except Exception as exc:  # noqa: BLE001 — said, never a silent zero
        print(f"note: tracker unavailable ({exc.__class__.__name__}) — ①d and ③ computed without it", file=sys.stderr)
    try:
        from .report import load_events

        events = load_events()
    except Exception:  # noqa: BLE001
        events = []
    try:
        sig = compute(args.epic, cwd=REPO, project=project, notes=notes, events=events, acceptance_for=acceptance_for, mode=args.mode)
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(sig) if args.json else render(sig))
    if args.record:
        from tracker.campaign import record

        ok = record(args.epic, payload(sig), outcome=args.outcome)
        print(f"  recorded as campaign.epic_{args.outcome}" if ok else "  NOT recorded — the telemetry port refused", file=sys.stderr)
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
