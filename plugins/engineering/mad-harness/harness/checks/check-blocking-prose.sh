#!/usr/bin/env bash
# Find records whose TEXT claims a blocking relationship that has no dependency EDGE.
#
#   harness/checks/check-blocking-prose.sh [--strict]     (advisory by default)
#
# A note saying "GATED ON X" gates nothing: the scheduler reads edges, not prose. The test
# is deliberately narrow — a claim only counts as broken if the record it says is blocked
# is dispatchable right now, so everything printed contradicts its own text today.
set -euo pipefail
# Record where we were invoked from BEFORE moving: the cd below lands in the
# harness, which carries its own harness.yaml, and the resolver would read that
# as the project. Already-set wins, so a caller may state it explicitly.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec uv run python -m tracker.check_blocking "$@"
