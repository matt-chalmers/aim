"""The campaign's epic queue and triage — `campaign-loop` §1 and §2 as one call.

WHAT THE ORCHESTRATOR DID BY HAND, once per run and three more calls per epic: list the
open epics, sort P0→P3 then oldest, `gate list` and drop every epic named there ("do this
explicitly, or the loop never terminates"), `lease list` and drop every epic another
machine holds, `lease acquire` before starting and `lease release` at close-out; then per
epic `show`, `list --parent --status open` and `ready --parent` to classify it UNPLANNED /
PARTIAL / READY — a pure function of three counts that also happens to be signal ⑤,
"dispatchable on entry", retyped by hand into the telemetry payload at §6.

WHAT WAS WRONG BESIDES THE COST. `campaign_auto.open_epics` did one of the five §1 steps
and ASSUMED `status=blocked` covered the gate exclusion — true only when the three-step
park always ran, which 0.10.19 found it did not. And nothing in code acquired or released
a lease: cross-machine double work was guarded by prose alone, while `lease.py`'s
docstring records the failure it exists to prevent (two campaigns claim the same task,
both merge, the export conflicts on push or silently takes the last write).

Exclusion is by every signal the loop names: a gate whose target is the epic, a `PARKED`
note on the epic (beads cannot record a gate's target, so the note is how `park` leaves
its mark), and a lease held by another host. A lease this machine holds is not an
exclusion — it is a resumed run's own epic.
"""

from __future__ import annotations

import json
import socket
import sys
from dataclasses import asdict, dataclass

UNPLANNED, PARTIAL, READY = "UNPLANNED", "PARTIAL", "READY"


@dataclass
class Entry:
    id: str
    title: str
    priority: int | None
    created_at: str
    excluded: str | None = None  # why, or None when the epic is runnable
    triage: str | None = None
    children_open: int = 0
    with_criteria: int = 0
    ready: int = 0

    @property
    def sort_key(self):
        return (self.priority is None, self.priority if self.priority is not None else 0, self.created_at, self.id)


def triage(children: list, ready: list) -> tuple[str, int, int, int]:
    """(state, open children, with acceptance criteria, ready) — §2's table as a function."""
    open_children = [c for c in children if c.status != "closed"]
    with_ac = sum(1 for c in open_children if (c.acceptance or "").strip())
    if not open_children:
        return UNPLANNED, 0, 0, 0
    n_ready = len(ready)
    if n_ready == 0 or with_ac == 0:
        return PARTIAL, len(open_children), with_ac, n_ready
    return READY, len(open_children), with_ac, n_ready


def build(store, *, leases: dict | None, host: str, parked_note=None) -> list[Entry]:
    """Every open epic, ordered, each excluded with its reason or triaged."""
    from tracker.port import PARKED

    gates = store.gate_list()
    gated: dict[str, str] = {}
    for g in gates:
        for target in g.depends_on:
            gated[target] = (g.title or g.description or g.id)[:80]
    epics = store.list(type="epic", status="open")
    out: list[Entry] = []
    for e in sorted(epics, key=lambda t: (t.priority is None, t.priority or 0, t.created_at or "", t.id)):
        entry = Entry(e.id, e.title, e.priority, e.created_at or "")
        if e.id in gated:
            entry.excluded = f"gated: {gated[e.id]}"
        elif PARKED.search(e.notes or ""):
            entry.excluded = "parked (a PARKED note; the gate holds it)"
        elif leases and e.id in leases and (leases[e.id].get("host") or "") not in ("", host):
            entry.excluded = f"leased by {leases[e.id].get('holder', '?')} on {leases[e.id].get('host', '?')}"
        else:
            state, n, ac, r = triage(store.list(parent=e.id), store.ready(parent=e.id))
            entry.triage, entry.children_open, entry.with_criteria, entry.ready = state, n, ac, r
        out.append(entry)
    return out


def runnable(entries: list[Entry]) -> list[Entry]:
    return [e for e in entries if e.excluded is None]


def render(entries: list[Entry], notes: list[str]) -> str:
    out = [f"epic queue: {len(runnable(entries))} runnable of {len(entries)} open, P0→P3 then oldest"]
    for e in entries:
        pri = f"P{e.priority}" if e.priority is not None else "P-"
        if e.excluded:
            out.append(f"  --  {e.id:<18} {pri}  EXCLUDED — {e.excluded}   {e.title[:50]}")
        else:
            out.append(f"  {e.triage:<9} {e.id:<18} {pri}  {e.ready} ready / {e.with_criteria} with criteria / {e.children_open} open   {e.title[:50]}")
    out += [f"  note: {n}" for n in notes]
    return "\n".join(out)


def load_leases(cwd: str | None = None) -> tuple[dict, str | None]:
    """epic -> {holder, host, at} for every lease on the remote; (None, why) when the
    remote cannot be reached — said, so the exclusion is known to be unchecked."""
    from tracker import lease

    try:
        held = lease.held(cwd)
    except lease.LeaseError as exc:
        return {}, f"leases NOT checked — {exc}"
    out = {}
    for epic in held:
        info = lease.inspect(epic, cwd=cwd)
        if info:
            out[epic] = {"holder": info.holder, "host": info.host, "at": info.at}
    return out, None


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="epic-queue.sh", description="campaign-loop §1 and §2 as one call: every open epic ordered P0→P3 then oldest, each excluded with its reason (gated, parked, leased elsewhere) or triaged UNPLANNED/PARTIAL/READY with its dispatchable-on-entry count.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-leases", action="store_true", help="do not consult the remote for leases")
    args = ap.parse_args(argv)
    import tracker

    notes: list[str] = []
    leases: dict | None = {}
    if not args.no_leases:
        leases, why = load_leases()
        if why:
            notes.append(why)
    try:
        entries = build(tracker.task_store(), leases=leases, host=socket.gethostname())
    except Exception as exc:  # noqa: BLE001 — a tracker that cannot answer is a stop, named, never an empty queue
        print(f"FAIL: could not read the epic queue — {exc.__class__.__name__}: {str(exc)[:200]}", file=sys.stderr)
        return 2
    print(json.dumps({"epics": [asdict(e) for e in entries], "notes": notes}) if args.json else render(entries, notes))
    return 0 if runnable(entries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
