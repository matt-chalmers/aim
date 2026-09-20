#!/usr/bin/env bash
# /swarm steps 2-3 as one call: the ready set for a lane, top n by priority clamped to the
# lane cap and CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS, the resume point of every candidate
# (MERGE needs no worker), shared existing paths dropped (lower priority loses), any file
# past signals.megafile_lines a lane of width one, candidate branches merge-tree'd — and
# the two checks that need judgement (shared vocabulary, new files) printed for you.
# With --parent it opens the epic's next wave manifest.
#
#   harness/swarm/wave-plan.sh <lane> [n] [--parent <epic>] [--json] [--no-manifest]
#
# Exit 0 a wave · 1 nothing ready (says why) · 2 lane not declared.
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.wave_plan "$@"
