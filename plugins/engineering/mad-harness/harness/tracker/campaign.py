"""Campaign health signals as an append-only series.

campaign-loop §6 defines the health signals and their bad values, but until there was a
series nothing evaluated them: they were narrated into a report that scrolls away. A
single bad epic is noise; a signal drifting across five is the finding, and that is
invisible without history.

STORED THROUGH THE TELEMETRY PORT, which merges anything the previous backend still holds.
These were `event` tasks, and they stay readable as such — the trend that makes this
instrument worth having is entirely in the past, so a move that started an empty series
would destroy it while appearing to work.
"""

from __future__ import annotations

import json
import sys

import tracker

#: One category per OUTCOME. There was one constant for every record, so an epic parked
#: before a single wave ran was filed as `epic_closed` with `beads_closed: 0` — and read
#: back as a catastrophically bad completed epic rather than a normal parked one. The
#: trend line was then drawn straight through closed and parked alike, which is exactly
#: the reading ("decision-blocked, not slow") §6 asks the report to be able to make.
OUTCOMES = ("closed", "parked", "stopped")
CATEGORIES = {o: f"campaign.epic_{o}" for o in OUTCOMES}
CATEGORY = CATEGORIES["closed"]  # the default, so rows recorded before outcomes existed keep their meaning

#: Straight from campaign-loop §6 — the bad values it already documents. Kept as data so
#: the thresholds and the prose cannot drift apart silently.
CHECKS: tuple[tuple[str, str, str, str], ...] = (
    ("first_pass_rate", "first-pass PASS %", "v > 90 or v < 40",
     ">90% sustained = soft gate · <40% = underspecified tasks"),
    ("escape_rate", "escape (fix:/revert:) %", "v > 20",
     ">20% or any revert: of a campaign commit"),
    ("wave_yield", "wave yield %", "v < 60", "<60%"),
    ("lines_per_ac", "changed lines per AC", "v > 400",
     ">400 = the slicing rule slipped"),
    ("merge_conflicts", "merge conflicts", "v > 0",
     "any conflict is a step-3 miss by definition"),
    ("dispatchable_on_entry", "dispatchable on entry", "v <= 2",
     "1 or 2 explains a bad cost ratio — the epic is decision-blocked"),
    ("l4_dispatch_rate", "L4 dispatch %", "v < 100",
     "a round that touched a declared security path where L4 never fired = a broken trigger"),
    ("analyst_gate_rate", "analyst gate %", "v < 100",
     "an epic reaching §3b without ADEQUACY: or §3e without AUDIT: is a skipped gate"),
)


def _bad(expr: str, v) -> bool:
    try:
        return bool(eval(expr, {"__builtins__": {}}, {"v": v}))  # noqa: S307 — fixed table
    except Exception:  # noqa: BLE001
        return False


def record(epic: str, payload: dict, outcome: str = "closed") -> bool:
    if outcome not in CATEGORIES:
        raise ValueError(f"outcome must be one of {', '.join(OUTCOMES)}, not {outcome!r}")
    return tracker.telemetry().record(CATEGORIES[outcome], epic, payload)


def rows() -> list[dict]:
    """Every outcome, each row tagged with its own, in recording order."""
    out = []
    for outcome, category in CATEGORIES.items():
        for e in tracker.telemetry().read(category):
            row = dict(e.payload)
            row["_epic"] = e.target
            row["_when"] = (e.at or "")[:10]
            row["_outcome"] = outcome
            row["_at"] = e.at or ""
            out.append(row)
    out.sort(key=lambda r: r["_at"])
    return out


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]

    if args and args[0] == "record":
        outcome = "closed"
        if "--outcome" in args:
            i = args.index("--outcome")
            outcome = args[i + 1] if i + 1 < len(args) else ""
            args = args[:i] + args[i + 2 :]
        if len(args) < 3 or outcome not in CATEGORIES:
            print(
                "usage: campaign-telemetry.sh record <epic-id> '<json payload>' "
                f"[--outcome {'|'.join(OUTCOMES)}]",
                file=sys.stderr,
            )
            return 2
        try:
            payload = json.loads(args[2])
        except json.JSONDecodeError as exc:
            print(f"payload is not valid JSON: {exc}", file=sys.stderr)
            return 1
        if not record(args[1], payload, outcome):
            print("could not record the event", file=sys.stderr)
            return 1
        print(f"recorded {CATEGORIES[outcome]} for {args[1]}")
        return 0

    data = rows()
    if not data:
        print(
            "No campaign telemetry recorded yet.\n"
            "Record one at epic close:  "
            "harness/campaign/campaign-telemetry.sh record <epic-id> '<json>'"
        )
        return 0

    width = max(len(str(r["_epic"])) for r in data) + 2
    header = f"{'epic':<{width}}{'date':<12}{'outcome':<9}" + "".join(
        f"{label[:20]:>22}" for _, label, _, _ in CHECKS
    )
    print(header)
    for r in data:
        line = f"{str(r['_epic']):<{width}}{r['_when']:<12}{r['_outcome']:<9}"
        for key, _, expr, _ in CHECKS:
            v = r.get(key)
            shown = "—" if v is None else (f"{v}!" if _bad(expr, v) else str(v))
            line += f"{shown:>22}"
        print(line)
    print("\n! = outside the band campaign-loop §6 documents")

    closed = [r for r in data if r["_outcome"] == "closed"]
    parked = [r for r in data if r["_outcome"] != "closed"]
    if parked:
        print(
            f"\n{len(parked)} epic(s) parked or stopped — their planning cost was paid and "
            f"no task landed. dispatchable-on-entry is the number to read for those; they "
            f"are excluded from the trend below, which is about epics that ran."
        )
    if len(closed) >= 3:
        print("\nTrend over the last 3 closed epics (a single bad epic is noise; a drift is not):")
        for key, label, expr, why in CHECKS:
            vs = [r.get(key) for r in closed[-3:] if r.get(key) is not None]
            if len(vs) < 3:
                continue
            direction = (
                "rising" if vs[-1] > vs[0] else "falling" if vs[-1] < vs[0] else "flat"
            )
            flag = "  ← " + why if _bad(expr, vs[-1]) else ""
            print(f"  {label:<26} {vs[0]} → {vs[-1]}  ({direction}){flag}")
    else:
        print(f"\n{len(closed)} closed epic(s) recorded — a trend needs at least 3.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
