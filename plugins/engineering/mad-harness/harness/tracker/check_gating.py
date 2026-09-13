"""How much of the backlog is buildable at all.

WHY THE NUMBER MATTERS. A campaign that lands one task per epic is not an inefficient
campaign — it is a campaign running against a gated queue, and the fix for that is
answering decisions, not tuning the pipeline. Reported every run as one line, so a low
task count is read correctly rather than as a process failure.

This was an inline `python3 -c` inside the campaign prompt, writing to a fixed /tmp path.
A prompt is a bad place for a program: nothing lints it, nothing tests it, and the
hardcoded scratch path assumed one machine's layout.
"""

from __future__ import annotations

import sys

import tracker
from tracker.port import DECISION

#: Above this share of the open queue waiting on the owner, the bottleneck is the
#: backlog rather than the pipeline, and the report should say so outright.
GATED_SHARE_WARNING = 0.25


def summarise(store=None) -> dict:
    store = store or tracker.task_store()
    open_tasks = [t for t in store.list(status="open", limit=500)]
    decisions = [t for t in open_tasks if t.type == DECISION]
    gates = store.gate_list()
    return {
        "open": len(open_tasks),
        "decisions": len(decisions),
        "buildable": len(open_tasks) - len(decisions),
        "gates": len(gates),
    }


def main(argv: list[str] | None = None) -> int:
    store = tracker.task_store()
    try:
        d = summarise(store)
    except tracker.TrackerError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    # An empty queue is a real answer here — a backlog can genuinely be empty — but it
    # is worth distinguishing from a tracker that answered nothing at all.
    if d["open"] == 0 and d["gates"] == 0:
        print(
            "no open records and no gates. Either the backlog is clear, or no tracker "
            "is configured for this repository — check before reading this as healthy.",
            file=sys.stderr,
        )
        return 1

    print(
        f"open {d['open']}  |  decisions+requirements {d['decisions']}  "
        f"|  buildable {d['buildable']}"
    )
    if d["open"] and d["decisions"] / d["open"] > GATED_SHARE_WARNING:
        print(
            f"  >{int(GATED_SHARE_WARNING * 100)}% of the open queue is waiting on the "
            f"owner. A low task count per epic"
        )
        print("  is the queue being gated, not the pipeline being slow.")
    print(f"open human gates: {d['gates']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
