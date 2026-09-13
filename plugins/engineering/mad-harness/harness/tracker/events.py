"""The cost and health series. ONE implementation, whatever backend holds the records.

WHY IT LEFT THE TASK STORE. Telemetry lives in tasks today for one reason: beads happens
to support an `event` issue type, so `dispatch.record()` wrote one. Nothing about the
series wants a task tracker — the records are append-only, never queried by dependency,
never claimed, never closed. Keeping it in `TaskStore` would oblige every future backend
to grow an event type it has no other use for.

CONTINUITY IS THE WHOLE RISK OF MOVING IT. `campaign-telemetry.sh` exists because "a
single bad epic is noise; a signal drifting across five is the finding" — the value is
entirely in the history. A migration that silently starts an empty series destroys exactly
what the instrument is for, and it would look like it worked.

So there is NO migration step. `legacy_reader` is injected, reads whatever the old backend
holds, and its events are merged on read. Nothing is copied, nothing can be half-copied,
and the day the old backend goes away the reader is simply not supplied.

TWO TIERS, mirroring the task store. Writes land in an untracked hot file, because they
happen several times a wave and committing each would bury the real diff. `snapshot()`
writes the durable, git-tracked copy, and the orchestrator calls it at the same wave
boundary it already syncs the tracker at — one actor, once per wave.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .locks import run_dir
from .port import Event


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe(category: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "-" for c in category)


class LocalTelemetry:
    """Append-only JSONL, one file per category.

    :param legacy_reader: optional callable returning historical events from a previous
        backend. Merged on read so a move costs no history and needs no import step.
    """

    def __init__(
        self,
        root: Path | None = None,
        legacy_reader: Callable[[], list[Event]] | None = None,
    ) -> None:
        self.root = Path(root) if root else run_dir() / "events"
        self._legacy = legacy_reader

    def _path(self, category: str) -> Path:
        return self.root / f"{_safe(category)}.jsonl"

    def record(self, category: str, target: str, payload: dict[str, Any]) -> bool:
        """Append one event. NEVER raises — telemetry must not fail real work.

        A dispatch that succeeded and then failed to record its own cost is still a
        successful dispatch, and turning that into an error would make the instrument
        more dangerous than the thing it measures.
        """
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            line = json.dumps(
                {
                    "category": category,
                    "target": target,
                    "at": _stamp(),
                    "payload": payload,
                },
                sort_keys=True,
            )
            with self._path(category).open("a") as fh:
                fh.write(line + "\n")
            return True
        except (OSError, TypeError, ValueError):
            return False

    def _local(self, category: str | None) -> list[Event]:
        out: list[Event] = []
        if not self.root.is_dir():
            return out
        files = (
            [self._path(category)] if category else sorted(self.root.glob("*.jsonl"))
        )
        for f in files:
            if not f.is_file():
                continue
            for line in f.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a torn final line is not a reason to lose the series
                out.append(
                    Event(
                        category=str(d.get("category") or ""),
                        target=str(d.get("target") or ""),
                        payload=d.get("payload") or {},
                        at=str(d.get("at") or ""),
                    )
                )
        return out

    def read(self, category: str | None = None) -> list[Event]:
        """Every event, oldest first: local plus whatever the legacy backend still holds."""
        events = self._local(category)
        if self._legacy is not None:
            try:
                legacy = self._legacy()
            except Exception:  # noqa: BLE001 — a dead legacy store must not hide new data
                legacy = []
            if category:
                legacy = [e for e in legacy if e.category == category]
            events = legacy + events
        return sorted(events, key=lambda e: e.at)

    def snapshot(self, dest: Path) -> int:
        """Write the durable, git-tracked copy. Called once per wave by the orchestrator.

        Whole-file rewrite rather than append: the merged view includes legacy events, so
        appending would duplicate them on every run. Returns the number of events written.
        """
        events = self.read()
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(
            "\n".join(
                json.dumps(
                    {
                        "category": e.category,
                        "target": e.target,
                        "at": e.at,
                        "payload": e.payload,
                    },
                    sort_keys=True,
                )
                for e in events
            )
            + ("\n" if events else "")
        )
        return len(events)
