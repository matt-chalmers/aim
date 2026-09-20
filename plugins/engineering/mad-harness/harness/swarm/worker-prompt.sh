#!/usr/bin/env bash
# Assemble a writer's prompt from what the tracker and the harness already hold: the task
# record verbatim, how to run and commit here, the epic's SPEC INDEX slice the task's
# SURFACE: line points at (pointers, never paraphrase), the field-guide keys that match, and
# for a fidelity task the auditor's measured defect list. What the orchestrator ADDS goes in
# --extra. dispatch.sh --task-prompt calls this itself.
#
#   harness/swarm/worker-prompt.sh <task> [--lane <lane>] [--worker N] [--extra <file>] [--out <path>]
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.worker_prompt "$@"
