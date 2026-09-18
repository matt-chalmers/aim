"""The campaign's pinned state — what a compaction must not lose, regenerated from the
tracker and git rather than remembered.

MEASURED. A campaign session's compaction summary kept 2 of the 9 task ids the previous
400 rows mentioned and dropped two that were being dispatched (`TipDonkey-h7mh`,
`TipDonkey-jra7`). The loop's own rules go the same way: `campaign-loop` reaches the
session through the Skill tool, as a message, and a message is exactly what compaction
summarises away. Workers never compact (0 of 33 transcripts); the orchestrator does,
once or twice per long session. This is that session's anchor.

Nothing here is written by the model. Claims, worktrees and the merge slot are read from
where the loop already records them, the resume point of each claimed task from git, and
the invariants from one marked block in the loop's own skill — so the anchor is right
after any compaction, including one the loop never saw coming. The SessionStart hook
(`hooks/hooks.json`, matcher `compact|resume|startup`) prints it into the new context;
when a compaction summary can be found in the transcript, it also names every pinned id
the summary dropped, which is the check the summary itself cannot make.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .resolve import PLUGIN_ROOT, REPO, _prompts_dir

START = "<!-- PINNED -->"
END = "<!-- END PINNED -->"
#: Worktree directory names the dispatcher and the sweep use; the task id follows.
WORKTREE = re.compile(r"/(?:harness-w\d+-|worktree-agent-)(?P<task>[^/\s]+)$")


def card() -> str:
    """The orchestrator card — the rules that make a large context cheap — from its
    canonical copy, so it never drifts from what the commands carry."""
    from .check_card import canonical

    try:
        return "# ORCHESTRATOR CARD — re-read after compaction\n\n" + canonical()
    except (OSError, RuntimeError):
        return ""


def invariants() -> str:
    """The loop's post-compaction rules, from the one place they are stated."""
    text = (_prompts_dir("skills") / "campaign-loop" / "SKILL.md").read_text()
    if START not in text or END not in text:
        return ""
    return text[text.index(START) + len(START) : text.index(END)].strip()


def worktrees(repo: Path = REPO) -> list[tuple[str, str]]:
    """(task, path) for every harness worktree git still lists."""
    try:
        out = subprocess.run(
            ["git", "worktree", "list", "--porcelain"], cwd=str(repo),
            capture_output=True, text=True, timeout=15,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.splitlines():
        if line.startswith("worktree "):
            m = WORKTREE.search(line[len("worktree ") :])
            if m:
                found.append((m.group("task"), line[len("worktree ") :]))
    return found


def in_flight() -> dict[str, Any]:
    """Everything a campaign leaves on disk while it runs. Empty when idle."""
    import tracker

    state: dict[str, Any] = {"claims": [], "slot": None, "worktrees": worktrees()}
    try:
        co = tracker.coordination()
        state["claims"] = co.claims()
        slot = co.slot_check()
        state["slot"] = None if slot.free else f"{slot.holder}{' (STALE)' if slot.stale else ''}"
    except Exception as exc:  # noqa: BLE001 — a tracker that cannot answer is reported, never fatal here
        state["error"] = str(exc)[:200]
    return state


def pinned_ids(state: dict[str, Any]) -> list[str]:
    ids = [c["task"] for c in state.get("claims", [])] + [t for t, _ in state.get("worktrees", [])]
    return sorted(set(ids))


def last_compact_summary(transcript: Path | None) -> str | None:
    if not transcript or not transcript.is_file():
        return None
    summary = None
    with transcript.open(errors="replace") as fh:
        for line in fh:
            if "isCompactSummary" not in line and "is_compact_summary" not in line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not (row.get("isCompactSummary") or row.get("is_compact_summary")):
                continue
            content = (row.get("message") or {}).get("content")
            text = content if isinstance(content, str) else "".join(
                str(c.get("text") or "") for c in content if isinstance(c, dict)
            ) if isinstance(content, list) else ""
            if text:
                summary = text
    return summary


def dropped(summary: str | None, ids: list[str]) -> list[str]:
    if summary is None:
        return []
    return [i for i in ids if i not in summary]


def render(state: dict[str, Any], summary: str | None = None, source: str = "") -> str:
    lines = ["# PINNED — regenerated from the tracker and git, not from memory", ""]
    busy = bool(state.get("claims") or state.get("worktrees") or state.get("slot"))
    if source:
        lines.append(f"Injected on `{source}`. " + (f"A campaign is in flight in {REPO}." if busy else f"Nothing is in flight in {REPO}."))
    if state.get("error"):
        lines.append(f"!! tracker unavailable: {state['error']}")
    missing = dropped(summary, pinned_ids(state))
    if summary is not None:
        if missing:
            lines.append(
                f"!! The compaction summary DROPPED {len(missing)} pinned id(s): {', '.join(missing)}. "
                f"Trust this block over the summary for anything it names."
            )
        else:
            lines.append("The compaction summary kept every pinned id.")
    lines.append("")
    lines.append("## Claims held")
    if state.get("claims"):
        for c in state["claims"]:
            liveness = "alive" if c.get("alive") else ("STALE" if c.get("stale") else "not provably alive")
            lines.append(f"- `{c['task']}` held by {c['holder']} on {c['host']}, {c['age_s'] // 60}m, {liveness}")
    else:
        lines.append("- none")
    lines.append(f"\n## Merge slot\n- {state['slot'] or 'free'}")
    lines.append("\n## Worktrees")
    if state.get("worktrees"):
        for task, path in state["worktrees"]:
            lines.append(f"- `{task}` → {path}")
    else:
        lines.append("- none")
    rules = invariants()
    if rules:
        lines.append("\n## The rules that still apply\n" + rules)
    # ABSOLUTE PATHS. Skill text gets `${CLAUDE_PLUGIN_ROOT}` substituted at load; hook
    # output is injected verbatim, so a placeholder here would reach the model as a shell
    # variable nothing sets.
    lines.append(
        f"\nBefore acting: re-read `{PLUGIN_ROOT}/skills/campaign-loop/SKILL.md` §4 for the wave "
        f"you were in, and ask `{PLUGIN_ROOT}/harness/swarm/resume-point.sh <id>` for every id above "
        "— never re-dispatch a task whose branch already carries commits."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="pinned.sh",
        description="The campaign's pinned state: claims, merge slot, worktrees, the loop's rules.",
    )
    ap.add_argument("--hook", action="store_true", help="read the SessionStart hook payload on stdin; silent when nothing is in flight")
    ap.add_argument("--always", action="store_true", help="print even when nothing is in flight")
    ap.add_argument("--transcript", help="a session transcript; its last compaction summary is checked for every pinned id")
    args = ap.parse_args(argv)

    source = ""
    transcript = Path(args.transcript) if args.transcript else None
    if args.hook:
        try:
            payload = json.load(sys.stdin)
        except (json.JSONDecodeError, OSError):
            payload = {}
        source = str(payload.get("source") or "")
        # A DISPATCHED AGENT IS NOT THE ORCHESTRATOR. The hook fires in every worker too
        # (`startup`), and during a wave claims are always held — so the loop's rules and
        # every sibling's worktree were landing in each worker's first message. The
        # payload names the agent a session runs as; when it does, this is not our reader.
        if payload.get("agent_type"):
            return 0
        if payload.get("transcript_path"):
            transcript = Path(str(payload["transcript_path"]))

    state = in_flight()
    busy = bool(state.get("claims") or state.get("worktrees") or state.get("slot"))
    # THE CARD, ON EVERY COMPACTION AND RESUME, CAMPAIGN OR NOT. The orchestrator card's
    # rules are about a large context, and a session that compacts has one by definition:
    # the $11.21 reference load that measured rule 1 happened in a session whose campaign
    # had just ended, so "in flight" would have missed it. ~270 tokens, once per
    # compaction, in sessions that already carry hundreds of thousands.
    parts = []
    if source in ("compact", "resume"):
        parts.append(card())
    if busy or args.always:
        summary = last_compact_summary(transcript) if source in ("compact", "") else None
        parts.append(render(state, summary=summary, source=source))
    if parts:
        print("\n\n".join(parts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
