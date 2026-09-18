#!/usr/bin/env bash
# Apply a planner's plan to the tracker in one call — campaign-loop §3e + §3f as a script,
# instead of 10-30 model-driven tool calls at the orchestrator's context price.
#
#   harness/swarm/apply-plan.sh <plan.md> --epic <id> [--dry-run] [--render <path>]
#
# Every tk.sh line in the plan's ```bash blocks, validated whole before anything is
# written, labels (`T1: … create`) resolved to the ids the tracker hands out; then
# `tk.sh validate <epic>` and, with --render, the epic's view. Exit 2 = refused, nothing
# written; 1 = a command, validate or render failed (a rerun skips labels already created).
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.apply_plan "$@"
