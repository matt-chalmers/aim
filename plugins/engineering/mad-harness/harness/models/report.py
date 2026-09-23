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
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        # A project's redefined `worker` is not the plugin's; the two never share a row.
        # A computed cost and the SDK's estimate are different measurements; a row that
        # mixed them would average a price against a guess.
        groups[
            (e.get("agent", "?"), e.get("tier", "?"), e.get("provider", "?"), e.get("tier_source") or "plugin",
             "priced" if str(e.get("cost_source") or "sdk").startswith("priced") else "sdk",
             str(e.get("billing") or "metered"))
        ].append(e)

    rows = []
    for (agent, tier, provider, source, costing, billing), es in groups.items():
        costs = [float(e.get("cost_usd") or 0) for e in es]
        turns = [int(e.get("turns") or 0) for e in es]
        fails = sum(1 for e in es if not e.get("ok"))
        escalations = sum(1 for e in es if e.get("escalated_from"))
        # THE CACHE, TOKEN-WEIGHTED across the group rather than averaged per dispatch,
        # so one expensive cold run is not hidden behind ten cheap warm ones.
        reads = sum(int(e.get("cache_read_tokens") or 0) for e in es)
        writes = sum(int(e.get("cache_creation_tokens") or 0) for e in es)
        fresh = sum(int(e.get("input_tokens") or 0) for e in es)
        prompt = reads + writes + fresh
        rows.append(
            {
                "agent": agent,
                "tier": tier,
                "provider": provider,
                "tier_source": source,
                "cost_source": costing,
                "billing": billing,
                "n": len(es),
                "total_usd": sum(costs),
                "mean_usd": sum(costs) / len(es),
                "mean_turns": sum(turns) / len(es),
                "fail_pct": round(100 * fails / len(es)),
                "escalations": escalations,
                "cache_hit_pct": round(100 * reads / prompt) if prompt else None,
                "cache_write_pct": round(100 * writes / prompt) if prompt else None,
                # A budget kill is not a failure the worker reported; it is the ceiling
                # cutting it off. Counted apart so a tier that keeps dying is visible.
                "budget_kills": sum(1 for e in es if e.get("terminal") == "budget"),
                # TOOL RESULTS AS A SHARE OF THE PROMPT, token-weighted like the cache
                # figures. Field: 28% per worker; lab: ~7%. Measured only where the
                # transcript was found, so a group can show "—" beside real costs.
                "result_share_pct": _result_share(es),
                "large_results": sum(int(e.get("large_results") or 0) for e in es),
                "cache_breaks": sum(int(e.get("cache_breaks") or 0) for e in es),
            }
        )
    return sorted(rows, key=lambda r: -r["total_usd"])


def _result_share(es: list[dict[str, Any]]) -> int | None:
    measured = [e for e in es if e.get("carried_result_tokens") is not None]
    if not measured:
        return None
    carried = sum(int(e.get("carried_result_tokens") or 0) for e in measured)
    prompt = sum(
        int(e.get("cache_read_tokens") or 0) + int(e.get("cache_creation_tokens") or 0) + int(e.get("input_tokens") or 0)
        for e in measured
    )
    return round(100 * carried / prompt) if prompt else None


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
    # TWO POCKETS, NEVER ONE SUM. A subscription-dollar consumed is real — the allowance it
    # came out of is finite and the work stops when it is gone — but it is not a metered
    # dollar, and a single total would state a number neither pocket paid.
    pockets: dict[str, float] = defaultdict(float)
    for r in rows:
        pockets[r["billing"]] += r["total_usd"]
    print(
        f"{'agent':<22}{'tier':<11}{'provider':<11}{'src':<9}{'$src':<8}{'n':>4}"
        f"{'total $':>10}{'mean $':>9}{'turns':>7}{'fail%':>7}{'esc':>5}"
        f"{'kills':>7}{'cache%':>8}{'write%':>8}{'results%':>10}{'large':>7}{'breaks':>8}"
    )
    pct = lambda v: "—" if v is None else str(v)  # noqa: E731
    for r in rows:
        print(
            f"{r['agent']:<22}{r['tier']:<11}{r['provider']:<11}{r['tier_source']:<9}{r['cost_source']:<8}{r['n']:>4}"
            f"{r['total_usd']:>10.3f}{r['mean_usd']:>9.4f}"
            f"{r['mean_turns']:>7.1f}{r['fail_pct']:>7}{r['escalations']:>5}"
            f"{r['budget_kills']:>7}{pct(r['cache_hit_pct']):>8}{pct(r['cache_write_pct']):>8}"
            f"{pct(r['result_share_pct']):>10}{r['large_results']:>7}{r['cache_breaks']:>8}"
        )
    total = sum(r["total_usd"] for r in rows)
    kills = sum(r["budget_kills"] for r in rows)
    print(f"\n{len(events)} dispatches, ${total:.2f} total, {kills} killed by the budget ceiling.")
    print(
        "cache% = prompt tokens served from cache; write% = written to cache at a premium. "
        "Workers in one wave that each start cold show as low cache%; a resumed agent as ~0."
    )
    print(
        "results% = the share of the prompt that was the agent's own tool results, re-read "
        "every later turn; large = results of 8k+ chars (a whole file, an unwindowed grep). "
        "Field workers ran at 28%; the fix is windowed reads, not a shorter prompt. "
        "breaks = requests that re-wrote a prefix the previous request had cached — a TTL "
        "expiry over a long test run, or something above the history changing."
    )
    if pockets:
        print("\n" + "  ·  ".join(
            f"{'subscription consumed' if k == 'subscription' else 'metered spend'}: ${v:.2f}"
            for k, v in sorted(pockets.items())
        ) + ("   (two pockets, deliberately not summed)" if len(pockets) > 1 else ""))
    print(
        "A tier is worth keeping when its fail% and escalations stay low. "
        "A cheap tier that escalates re-pays the whole fixed base, so it is a "
        "loss well before its failure rate looks alarming."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
