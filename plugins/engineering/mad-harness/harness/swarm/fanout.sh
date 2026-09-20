#!/usr/bin/env bash
# Run N commands at once — one per line of a jobs file, no shell — each with a timeout,
# and answer for every one (a job past its timeout is HUNG, never left to hold the wave).
# --detach starts them under a supervisor in its own session; --wait <id> blocks up to
# --timeout and exits 5 while any is still running, so a headless orchestrator makes the
# same one call until it exits 0 or 1 — never a sleep loop.
#
#   harness/swarm/fanout.sh --jobs <file> [--cap N] [--timeout S] [--wave <epic>-w<n>]
#   harness/swarm/fanout.sh --detach --jobs <file> [--cap N] [--timeout S]
#   harness/swarm/fanout.sh --wait <run-id> [--timeout 540] [--wave <epic>-w<n>]
#
# Exit 0 every job ok · 1 any not ok or HUNG · 2 a refused line / usage · 5 still running.
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.fanout "$@"
