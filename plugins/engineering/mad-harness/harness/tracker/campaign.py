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

CATEGORY = "campaign.epic_closed"

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
)


def _bad(expr: str, v) -> bool:
    try:
        return bool(eval(expr, {"__builtins__": {}}, {"v": v}))  # noqa: S307 — fixed table
    except Exception:  # noqa: BLE001
        return False


def record(epic: str, payload: dict) -> bool:
    return tracker.telemetry().record(CATEGORY, epic, payload)


def rows() -> list[dict]:
    out = []
    for e in tracker.telemetry().read(CATEGORY):
        row = dict(e.payload)
        row["_epic"] = e.target
        row["_when"] = (e.at or "")[:10]
        out.append(row)
    return out


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]

    if args and args[0] == "record":
        if len(args) < 3:
            print(
                "usage: campaign-telemetry.sh record <epic-id> '<json payload>'",
                file=sys.stderr,
            )
            return 2
        try:
            payload = json.loads(args[2])
        except json.JSONDecodeError as exc:
            print(f"payload is not valid JSON: {exc}", file=sys.stderr)
            return 1
        if not record(args[1], payload):
            print("could not record the event", file=sys.stderr)
            return 1
        print(f"recorded {CATEGORY} for {args[1]}")
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
    header = f"{'epic':<{width}}{'date':<12}" + "".join(
        f"{label[:20]:>22}" for _, label, _, _ in CHECKS
    )
    print(header)
    for r in data:
        line = f"{str(r['_epic']):<{width}}{r['_when']:<12}"
        for key, _, expr, _ in CHECKS:
            v = r.get(key)
            shown = "—" if v is None else (f"{v}!" if _bad(expr, v) else str(v))
            line += f"{shown:>22}"
        print(line)
    print("\n! = outside the band campaign-loop §6 documents")

    if len(data) >= 3:
        print("\nTrend over the last 3 epics (a single bad epic is noise; a drift is not):")
        for key, label, expr, why in CHECKS:
            vs = [r.get(key) for r in data[-3:] if r.get(key) is not None]
            if len(vs) < 3:
                continue
            direction = (
                "rising" if vs[-1] > vs[0] else "falling" if vs[-1] < vs[0] else "flat"
            )
            flag = "  ← " + why if _bad(expr, vs[-1]) else ""
            print(f"  {label:<26} {vs[0]} → {vs[-1]}  ({direction}){flag}")
    else:
        print(f"\n{len(data)} epic(s) recorded — a trend needs at least 3.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
