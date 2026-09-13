#!/usr/bin/env bash
# Warn before a tracker record hits the ceiling that write-locks it.
#
#   harness/checks/check-record-size.sh [--strict]     (advisory by default)
#
# The ceiling comes from the backend's declared capabilities, not from a constant: it is
# a fact about tasks (~64KB, failing closed and silently), and a backend with no such
# limit disables the check with a line saying so rather than warning about nothing.
set -euo pipefail
# Record where we were invoked from BEFORE moving: the cd below lands in the
# harness, which carries its own harness.yaml, and the resolver would read that
# as the project. Already-set wins, so a caller may state it explicitly.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec uv run python -m tracker.check_size "$@"
