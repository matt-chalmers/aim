"""The circuit breakers — `campaign-loop` §4.5 — as arithmetic over the wave manifests.

SEVEN COUNTERS THAT LIVED IN A CONTEXT. "Wave gate red twice in a row", "the same task
FAILs the lenses twice", "a task enters a THIRD lens round", "a `decision` task appears",
`MAX_WAVES`, "zero tasks closed in a wave", "three epics parked consecutively" — every trip
condition is a count across waves, and until 0.10.22 the only place those counts existed
was the orchestrator's context, which the same skill says a compaction loses (a summary
kept 2 of 9 task ids). The recorded consequence is "overriding it three times across
three tasks in one run". Whether a breaker tripped is arithmetic over the record; what to
DO about it — gate which task, split along which seam, park, stop — is judgement, and the
table's prescribed action is printed beside each trip for the orchestrator to take or
override, saying so.

Exit 0 nothing tripped · 1 tripped, the action is yours · 2 a hard park prescribed
· 3 stop the whole run.
"""

from __future__ import annotations

import json
import sys

from . import wave_manifest as wm

EXIT_CLEAR, EXIT_TRIPPED, EXIT_PARK, EXIT_STOP = 0, 1, 2, 3
DEFAULT_MAX_WAVES = 6


def evaluate(waves: list[dict], *, ready_now: int | None, open_decisions: list[str], recent_outcomes: list[str], max_waves: int = DEFAULT_MAX_WAVES) -> list[dict]:
    """Every breaker, checked; a list of trips with their prescribed action and level."""
    trips: list[dict] = []
    if not waves:
        return trips
    last = waves[-1]

    gates = [(w.get("gate") or {}).get("status") for w in waves]
    if len(gates) >= 2 and gates[-1] == "red" and gates[-2] == "red":
        culprits = sorted({t for w in waves[-2:] for t in ((w.get("gate") or {}).get("attributed") or [])})
        trips.append({"breaker": "wave gate red twice in a row", "level": EXIT_TRIPPED,
                      "action": f"gate the culprit task ({', '.join(culprits) or 'attribution empty — read the gate logs'}), then re-check `tk.sh ready --parent <epic>`"})

    rounds_by_task: dict[str, list[dict]] = {}
    for w in waves:
        for task, rounds in (w.get("lenses") or {}).items():
            rounds_by_task.setdefault(task, []).extend(rounds)
    for task, rounds in sorted(rounds_by_task.items()):
        fails = sum(1 for r in rounds if any(r.get(k) == "FAIL" for k in ("L1", "L2", "L3", "L4")))
        if len(rounds) >= 3:
            trips.append({"breaker": f"{task} entered a THIRD lens round", "level": EXIT_TRIPPED,
                          "action": f"SPLIT {task} along the seams the rounds revealed (`tk.sh supersede <old> --with <new>`); land the part that is already lens-clear; gate it only if it is genuinely indivisible. Do not remediate a fourth time. Record it against signal ③."})
        elif fails >= 2:
            trips.append({"breaker": f"{task} FAILed the lenses twice", "level": EXIT_TRIPPED,
                          "action": f"gate {task} (not the epic), continue with the rest"})

    if open_decisions:
        trips.append({"breaker": f"a `decision` task appeared ({', '.join(open_decisions[:4])})", "level": EXIT_TRIPPED,
                      "action": "the hard line: `tk.sh park <epic> --reason …` after gating what the decision blocks; then re-check what is still ready"})

    if len(waves) >= max_waves:
        trips.append({"breaker": f"MAX_WAVES = {max_waves} reached ({len(waves)} waves)", "level": EXIT_PARK,
                      "action": "`tk.sh park <epic> --reason \"needs another campaign run\"` — a budget signal, not a dependency one"})

    if last.get("closed_at") and not last.get("closed"):
        if ready_now == 0:
            trips.append({"breaker": "zero tasks closed in the last wave and nothing is ready", "level": EXIT_PARK,
                          "action": "`tk.sh park <epic>` — gate what blocked; the re-check came back empty"})
        else:
            trips.append({"breaker": "zero tasks closed in the last wave", "level": EXIT_TRIPPED,
                          "action": f"gate what blocked, then compose the next wave from what remains — {ready_now if ready_now is not None else '?'} task(s) still ready; park only if that comes back empty"})

    if len(recent_outcomes) >= 3 and all(o == "parked" for o in recent_outcomes[-3:]):
        trips.append({"breaker": "three epics parked consecutively", "level": EXIT_STOP,
                      "action": "stop the whole run — something systemic is wrong; say so in the report"})
    return trips


def render(epic: str, waves: list[dict], trips: list[dict]) -> tuple[str, int]:
    head = f"breakers {epic}: {len(waves)} wave(s) on record"
    if not trips:
        return f"{head}\n  none tripped", EXIT_CLEAR
    lines = [head]
    for t in trips:
        mark = {EXIT_TRIPPED: "TRIP", EXIT_PARK: "PARK", EXIT_STOP: "STOP"}[t["level"]]
        lines.append(f"  [{mark}] {t['breaker']}\n         → {t['action']}")
    level = max(t["level"] for t in trips)
    lines.append({EXIT_TRIPPED: "\nThe action is yours. Overriding a breaker is allowed; say in the report that you did, why, and that it is an override.",
                  EXIT_PARK: "\nA hard park is prescribed — a budget or dependency signal, not a judgement.",
                  EXIT_STOP: "\nSTOP the run."}[level])
    return "\n".join(lines), level


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="breakers.sh", description="campaign-loop §4.5's circuit breakers, checked over the wave manifests; each trip printed with its prescribed action.")
    ap.add_argument("epic")
    ap.add_argument("--max-waves", type=int, default=DEFAULT_MAX_WAVES)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        waves = wm.all_waves(args.epic)
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    ready_now: int | None = None
    decisions: list[str] = []
    outcomes: list[str] = []
    try:
        import tracker

        store = tracker.task_store()
        ready_now = len(store.ready(parent=args.epic))
        opened = waves[0].get("opened_at", "") if waves else ""
        decisions = [t.id for t in store.list(type="decision", status="open") if not opened or (t.created_at or "") >= opened]
        from tracker.campaign import rows

        outcomes = [r.get("_outcome", "") for r in rows()][-3:]
    except Exception as exc:  # noqa: BLE001 — a tracker outage leaves those breakers unknown, said, not zero
        print(f"note: tracker unavailable ({exc.__class__.__name__}) — the decision, ready and parked-epic breakers were not checked", file=sys.stderr)
    trips = evaluate(waves, ready_now=ready_now, open_decisions=decisions, recent_outcomes=outcomes, max_waves=args.max_waves)
    text, code = render(args.epic, waves, trips)
    print(json.dumps({"trips": trips, "exit": code}) if args.json else text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
