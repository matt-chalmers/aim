"""Read back the dispatch cost series, so routing becomes measured not argued.

This repo has a recorded habit of correcting process beliefs by measuring them —
swarm.md's cost model (`tokens ~= 18,700 + 2,600 x tool_calls`) replaced a
plausible claim that had been steering effort at the wrong lever for months. Tier
assignment deserves the same treatment: which agents are actually cheap to run
cheaply is an empirical question, and until there is a series nobody can answer it.

Reads `harness.dispatch` event tasks written by dispatch.record(). Deliberately a
sibling of campaign-telemetry.sh rather than part of it: that script tracks the
six campaign health signals per epic, this one tracks per-dispatch spend.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def load_events(cwd: str | None = None) -> list[dict[str, Any]]:
    """The dispatch cost series, oldest first.

    Reads through the telemetry port, which merges events recorded locally with any the
    previous backend still holds — so the series spans the move rather than restarting
    at it. `_when` is kept for the report's date column.
    """
    import tracker

    out = []
    for e in tracker.telemetry().read("harness.dispatch"):
        payload = dict(e.payload)
        payload["_when"] = (e.at or "")[:10]
        out.append(payload)
    return out


def summarise(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per agent+tier: count, total and mean cost, mean turns, failure rate."""
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        groups[
            (e.get("agent", "?"), e.get("tier", "?"), e.get("provider", "?"))
        ].append(e)

    rows = []
    for (agent, tier, provider), es in groups.items():
        costs = [float(e.get("cost_usd") or 0) for e in es]
        turns = [int(e.get("turns") or 0) for e in es]
        fails = sum(1 for e in es if not e.get("ok"))
        escalations = sum(1 for e in es if e.get("escalated_from"))
        rows.append(
            {
                "agent": agent,
                "tier": tier,
                "provider": provider,
                "n": len(es),
                "total_usd": sum(costs),
                "mean_usd": sum(costs) / len(es),
                "mean_turns": sum(turns) / len(es),
                "fail_pct": round(100 * fails / len(es)),
                "escalations": escalations,
            }
        )
    return sorted(rows, key=lambda r: -r["total_usd"])


def main() -> int:
    events = load_events()
    if not events:
        print(
            "No dispatch telemetry yet.\n"
            "Every dispatch through harness/models/dispatch.sh records one "
            "`harness.dispatch` event task automatically."
        )
        return 0

    rows = summarise(events)
    print(
        f"{'agent':<22}{'tier':<11}{'provider':<11}{'n':>4}"
        f"{'total $':>10}{'mean $':>9}{'turns':>7}{'fail%':>7}{'esc':>5}"
    )
    for r in rows:
        print(
            f"{r['agent']:<22}{r['tier']:<11}{r['provider']:<11}{r['n']:>4}"
            f"{r['total_usd']:>10.3f}{r['mean_usd']:>9.4f}"
            f"{r['mean_turns']:>7.1f}{r['fail_pct']:>7}{r['escalations']:>5}"
        )
    total = sum(r["total_usd"] for r in rows)
    print(f"\n{len(events)} dispatches, ${total:.2f} total.")
    print(
        "A tier is worth keeping when its fail% and escalations stay low. "
        "A cheap tier that escalates re-pays the whole fixed base, so it is a "
        "loss well before its failure rate looks alarming."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
