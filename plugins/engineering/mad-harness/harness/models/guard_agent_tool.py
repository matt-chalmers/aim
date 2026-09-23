"""Refuse the Agent tool for this plugin's agents — they run through `dispatch.sh`.

MEASURED. TipDonkey's five September campaign sessions: plugin agents spawned through
the Agent tool read 67.2M prompt tokens; through `dispatch.sh`, 6.9M. Eighteen of ~22
architect, planner and analyst runs took the Agent-tool path — with no `--max-budget-usd`
ceiling, no tier routing, no sandbox, no `harness.dispatch` record, and none of the
levers this harness measures. `/swarm` step 5 had said "not the Agent tool" all along;
`campaign-loop` §3 said "dispatch" without saying how. Doctrine failed once, so this is
the rule as a mechanism: a `PreToolUse` hook (hooks/hooks.json) that denies the call
with the form to use instead. Deny with a reason, never widen — the broker's pattern.

Only this plugin's agents are refused. Built-in and other plugins' agents pass; a session
without the plugin never runs this.

AGENT TEAMS ARE NOT AN EXEMPTION (0.10.31, decided after measuring). A teammate's output
does reach the lead only by message, so the measured reason above — a result landing in the
caller's context — does not apply to one. Everything else the boundary gives still does:
no tier effort (a teammate inherits the lead's), no ceiling, no sandbox, no cost record,
and not even the definition's model variant (observed: `claude-opus-5` where the definition
says `claude-opus-5[1m]`). A `/design-debate` command was built, measured against `/design`
on the same lab epic, and removed: ~$33 against ~$14, no measurable difference in the
design, and zero `harness.dispatch` events for $21 of it. The numbers are in
docs/upgrading.md 0.10.31; the assessment is in docs/concepts/architecture.md. This hook
refuses every one of this plugin's agents, without exception.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from .resolve import PLUGIN_ROOT, plugin_name

#: The tool the CLI spawns subagents with, under both of its names.
AGENT_TOOLS = ("Agent", "Task")


def _plugin_agents() -> set[str]:
    agents = PLUGIN_ROOT / "agents"
    return {p.stem for p in agents.glob("*.md")} if agents.is_dir() else set()


def decision(payload: dict[str, Any]) -> dict[str, Any] | None:
    """The hook's verdict for one tool call, or None to say nothing (allow)."""
    if payload.get("tool_name") not in AGENT_TOOLS:
        return None
    requested = str((payload.get("tool_input") or {}).get("subagent_type") or "")
    if not requested:
        return None
    name = plugin_name()
    bare = requested.split(":", 1)[1] if name and requested.startswith(f"{name}:") else requested
    if ":" in requested and not (name and requested.startswith(f"{name}:")):
        return None  # another plugin's agent
    if bare not in _plugin_agents():
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"{requested} is a {name or 'harness'} agent and runs through the dispatcher, not the Agent "
                f"tool — that is where its tier, its --max-budget-usd ceiling, its sandbox and its cost "
                f"record live (measured: 67.2M tokens went through the Agent tool unrecorded and uncapped). "
                f"Write the prompt to a file, then: {PLUGIN_ROOT}/harness/models/dispatch.sh {bare} "
                f"--prompt-file <path> [--task <id>] — as a background Bash call, and collect its output."
            ),
        }
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    verdict = decision(payload if isinstance(payload, dict) else {})
    if verdict:
        print(json.dumps(verdict))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
