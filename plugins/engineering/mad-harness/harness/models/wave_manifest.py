"""One wave's record on disk — what was planned, dispatched, judged, merged and closed.

WHY A FILE. `campaign-loop` §4.5 lists seven circuit breakers — "wave gate red twice in a
row", "the same task FAILs the lenses twice", "a task enters a THIRD lens round",
`MAX_WAVES`, "zero tasks closed in a wave", "three epics parked consecutively" — and every
one is a counter across waves that lived only in the orchestrator's context, which the
same skill says a compaction loses (a summary kept 2 of 9 task ids). `/swarm` step 10's
four health signals are ratios over the same facts, and "a signal you do not write down
is not a signal" was true of them: nothing wrote them down. This file is where the wave
scripts write what they did, so the breakers and the signals are arithmetic over a record
rather than a recollection.

WHO WRITES WHAT. Each wave script owns its keys and appends under a lock, because the
lens gates for a wave's tasks run concurrently:

    wave_plan     planned, dropped, lane, wave_base
    fanout        dispatched[task]
    lens_gate     lenses[task][]           a LIST per task, so rounds are countable
    merge_wave    merged, conflicts, gate
    close_wave    closed, head, closed_at

`/grind` has no wave: every writer's `--wave` is optional and its absence is printed.

WHERE. `<run_dir>/waves/<epic>-w<n>.json`, the run dir being the PRIMARY checkout's —
the rule `apply_plan.State.load` follows — so a lens gate started from a worktree and a
close run from the primary find the same file.
"""

from __future__ import annotations

import fcntl
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

NAME = re.compile(r"^(?P<epic>.+)-w(?P<n>\d+)$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def waves_dir(base: Path | None = None) -> Path:
    if base is not None:
        return base / "waves"
    from tracker.locks import run_dir
    from tracker.port import TrackerError

    try:
        return run_dir() / "waves"
    except TrackerError:
        from .resolve import repo_root

        return repo_root() / ".harness" / "run" / "waves"


def path_for(name_or_path: str, base: Path | None = None) -> Path:
    """`<epic>-w<n>` under the waves dir, or a path as given."""
    p = Path(name_or_path)
    if p.suffix == ".json" or "/" in name_or_path:
        return p
    return waves_dir(base) / f"{name_or_path}.json"


def open_wave(epic: str, *, lane: str, planned: list[str], dropped: list[dict], wave_base: str, base: Path | None = None) -> Path:
    """Start the next wave for `epic`: n = one past the highest that exists."""
    d = waves_dir(base)
    d.mkdir(parents=True, exist_ok=True)
    n = 1 + max((int(m.group("n")) for p in d.glob(f"{epic}-w*.json") if (m := NAME.match(p.stem))), default=0)
    path = d / f"{epic}-w{n}.json"
    doc = {
        "epic": epic, "wave": n, "lane": lane, "opened_at": _now(), "wave_base": wave_base,
        "planned": list(planned), "dropped": list(dropped),
        "dispatched": {}, "lenses": {}, "merged": [], "conflicts": [], "gate": None,
        "closed": [], "head": None, "closed_at": None,
    }
    path.write_text(json.dumps(doc, indent=2) + "\n")
    return path


def current(epic: str, base: Path | None = None) -> Path | None:
    """The highest-numbered wave for `epic` that is not closed, or None."""
    d = waves_dir(base)
    best: tuple[int, Path] | None = None
    for p in d.glob(f"{epic}-w*.json"):
        m = NAME.match(p.stem)
        if not m:
            continue
        try:
            if json.loads(p.read_text()).get("closed_at"):
                continue
        except (OSError, ValueError):
            continue
        n = int(m.group("n"))
        if best is None or n > best[0]:
            best = (n, p)
    return best[1] if best else None


def all_waves(epic: str, base: Path | None = None) -> list[dict[str, Any]]:
    """Every wave for `epic`, in order. A malformed file is an error, not an empty wave."""
    out = []
    for p in sorted(waves_dir(base).glob(f"{epic}-w*.json"), key=lambda p: int(NAME.match(p.stem).group("n")) if NAME.match(p.stem) else 0):
        try:
            out.append(load(p))
        except ValueError as exc:
            raise ValueError(f"{p}: {exc}") from exc
    return out


def load(path: Path) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed wave manifest: {exc}") from exc
    if not isinstance(doc, dict) or "epic" not in doc or "wave" not in doc:
        raise ValueError("malformed wave manifest: not a wave record")
    return doc


def _locked(path: Path, mutate) -> dict[str, Any]:
    """Read-modify-write under an exclusive lock on `<path>.lock`."""
    lock = path.with_suffix(path.suffix + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            doc = load(path)
            mutate(doc)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(doc, indent=2) + "\n")
            tmp.replace(path)
            return doc
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def set_key(path: Path, key: str, value: Any) -> dict[str, Any]:
    return _locked(path, lambda d: d.__setitem__(key, value))


def append(path: Path, key: str, value: Any, *, task: str | None = None) -> dict[str, Any]:
    """`lenses[task].append(value)` / `merged.append(value)` / `dispatched[task] = value`."""

    def mutate(d: dict[str, Any]) -> None:
        if task is None:
            d.setdefault(key, []).append(value)
        elif key == "dispatched":
            d.setdefault(key, {})[task] = value
        else:
            d.setdefault(key, {}).setdefault(task, []).append(value)

    return _locked(path, mutate)


def close(path: Path, head: str) -> dict[str, Any]:
    def mutate(d: dict[str, Any]) -> None:
        d["head"] = head
        d["closed_at"] = _now()

    return _locked(path, mutate)


def heartbeat(path: Path, phase: str, detail: str = "", runner=None) -> str | None:
    """`tk.sh note <epic> "wave <n>: <PHASE> <detail> @<time>"` — the phase heartbeat §0
    asked the orchestrator to write before each of five phases per wave ("two seconds a
    wave; without it, '8.5 hours with zero activity' tells you it stalled but not WHERE").
    Written by the script that runs the phase, so it cannot be skipped. Returns the reason
    it was not written, or None."""
    import subprocess

    from .resolve import HARNESS

    try:
        doc = load(path)
    except (OSError, ValueError) as exc:
        return str(exc)
    line = f"wave {doc['wave']}: {phase} {detail} @{_now()}".replace("  ", " ")
    run = runner or subprocess.run
    try:
        proc = run([str(HARNESS / "tracker" / "tk.sh"), "note", doc["epic"], line], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)[:200]
    return None if proc.returncode == 0 else (proc.stderr or proc.stdout).strip()[:200]


def summary_line(doc: dict[str, Any]) -> str:
    """One line for the pinned state: what a compaction must not lose about the wave."""
    verified = sum(1 for rounds in doc.get("lenses", {}).values() if rounds and rounds[-1].get("verified"))
    return (
        f"wave {doc['epic']}-w{doc['wave']} {'closed' if doc.get('closed_at') else 'open'}: "
        f"{len(doc.get('planned', []))} planned, {len(doc.get('dispatched', {}))} dispatched, "
        f"{verified} verified, {len(doc.get('merged', []))} merged, {len(doc.get('closed', []))} closed"
    )
