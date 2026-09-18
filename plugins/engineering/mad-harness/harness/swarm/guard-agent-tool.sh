#!/usr/bin/env bash
# PreToolUse hook: this plugin's agents run through dispatch.sh, never the Agent tool.
# Reads the hook payload on stdin; prints a deny decision, with the form to use instead,
# for a mad-harness subagent_type and nothing otherwise. See models/guard_agent_tool.py.
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.guard_agent_tool "$@"
