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

THE ONE EXEMPTION — AGENT TEAMS, FOR THE DEBATE EXPERIMENT (0.10.31). A teammate is not a
subagent: its output reaches the lead only by message, never as a result landing in the
lead's context, which is the measured reason above. The other reasons still hold — no tier
effort, no ceiling, no sandbox, no cost record — and are accepted only for an interactive
experiment with a person watching: `/design-debate`, an architect and an analyst arguing
a design. So `architect` and `analyst` pass when BOTH `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`
(teams on) and `MAD_HARNESS_TEAMS_DEBATE=1` (this experiment, set by the person for the
session) are in the hook's environment; every other agent, and every other session, is
refused as before. Whether a teammate spawn reaches this hook at all, and with what
payload, the docs do not say: `MAD_HARNESS_HOOK_TRACE=1` appends every payload seen to
`.harness/run/events/harness.hook.jsonl`, and the exemption narrows to the marker that
trace shows once one has been read.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .resolve import PLUGIN_ROOT, REPO, plugin_name

#: The tool the CLI spawns subagents with, under both of its names.
AGENT_TOOLS = ("Agent", "Task")
#: The two agents `/design-debate` runs as teammates. Nothing else is ever exempt.
DEBATE_AGENTS = frozenset({"architect", "analyst"})
TRACE = REPO / ".harness" / "run" / "events" / "harness.hook.jsonl"


def debate_exempt(bare: str, environ: dict[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return (
        bare in DEBATE_AGENTS
        and bool(env.get("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"))
        and env.get("MAD_HARNESS_TEAMS_DEBATE") == "1"
    )


def trace(payload: dict[str, Any], environ: dict[str, str] | None = None, path: Path | None = None) -> None:
    """Under MAD_HARNESS_HOOK_TRACE=1, the payload as received — the observation the teams
    exemption is refined from. Never raises: a trace must not break a tool call."""
    env = os.environ if environ is None else environ
    if env.get("MAD_HARNESS_HOOK_TRACE") != "1":
        return
    try:
        path = path or TRACE
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as fh:
            fh.write(json.dumps({"tool_name": payload.get("tool_name"), "tool_input": payload.get("tool_input"),
                                 "keys": sorted(payload)}) + "\n")
    except OSError:
        pass


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
    if debate_exempt(bare):
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
                f"--prompt-file <path> [--task <id>] — as a background Bash call, and collect its output. "
                f"(The one exemption: architect and analyst as teammates under /design-debate, with "
                f"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS and MAD_HARNESS_TEAMS_DEBATE=1 set.)"
            ),
        }
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    payload = payload if isinstance(payload, dict) else {}
    trace(payload)
    verdict = decision(payload)
    if verdict:
        print(json.dumps(verdict))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
