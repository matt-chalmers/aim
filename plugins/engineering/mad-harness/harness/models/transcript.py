"""What a dispatch's tool results cost it — read back from the session transcript.

The SDK's result carries token totals per request and nothing per tool result. But a
tool result is not paid once: every later turn re-reads it, so its weight is
chars × turns-remaining, not chars. That number is the one the field cost analysis
calls "tool-result volume" and ranks second of eleven, and nothing recorded it.

Measured on two field workers (TipDonkey, 0.9.6): 205k chars of results per session,
carried ≈1.7M of 6.2M input tokens — 28%. None of it was test output, which workers
already `| tail`: it was the memories index at turn 2 (18k chars, carried the whole
session), whole-file reads (38k, 54k) and `grep -A 400` on a large document, three
times for the same section. The lab's workers: 21k chars, 6%, nothing over 8k. So this
is field telemetry; the lab cannot show what it measures.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: A result this size or larger is one the worker should have windowed or offloaded.
LARGE_CHARS = 8_000


@dataclass(frozen=True)
class ResultVolume:
    results: int = 0
    chars: int = 0
    large: int = 0
    #: chars × later turns ÷ 4 — the tokens those results added to every prompt after
    #: the one that fetched them. An estimate; the transcript does not carry tokens.
    carried_tokens: int = 0
    by_tool: dict[str, int] = field(default_factory=dict)

    def telemetry(self) -> dict[str, Any]:
        return {
            "tool_results": self.results,
            "tool_result_chars": self.chars,
            "large_results": self.large,
            "carried_result_tokens": self.carried_tokens,
            "result_chars_by_tool": dict(sorted(self.by_tool.items(), key=lambda kv: -kv[1])),
        }


def projects_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"


def session_file(cwd: str | Path, session_id: str) -> Path | None:
    """The CLI keeps `<projects>/<cwd with / and . as ->/<session>.jsonl`. Best-effort:
    a layout change means no measurement, never a failed dispatch."""
    if not session_id:
        return None
    slug = re.sub(r"[/.]", "-", str(Path(cwd).resolve()))
    direct = projects_dir() / slug / f"{session_id}.jsonl"
    if direct.is_file():
        return direct
    for p in projects_dir().glob(f"*/{session_id}.jsonl"):
        return p
    return None


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(x.get("text") or "") for x in content if isinstance(x, dict))
    return ""


def result_volume(path: Path) -> ResultVolume:
    """One pass over a transcript: every tool result's size and the turn it arrived in."""
    turns = 0
    names: dict[str, str] = {}
    seen: list[tuple[int, int, str]] = []
    with path.open(errors="replace") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = row.get("message") or {}
            if row.get("type") == "assistant":
                turns += 1
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use":
                    names[str(c.get("id"))] = str(c.get("name") or "?")
                elif c.get("type") == "tool_result":
                    seen.append((turns, len(_text(c.get("content"))), names.get(str(c.get("tool_use_id")), "?")))
    by_tool: dict[str, int] = {}
    for _, n, tool in seen:
        by_tool[tool] = by_tool.get(tool, 0) + n
    return ResultVolume(
        results=len(seen),
        chars=sum(n for _, n, _ in seen),
        large=sum(1 for _, n, _ in seen if n >= LARGE_CHARS),
        carried_tokens=sum(n * (turns - t) for t, n, _ in seen) // 4,
        by_tool=by_tool,
    )


def result_volume_for(cwd: str | Path, session_id: str) -> ResultVolume | None:
    """None when the transcript cannot be found — recorded as absent, never as zero."""
    path = session_file(cwd, session_id)
    if path is None:
        return None
    try:
        return result_volume(path)
    except OSError:
        return None
