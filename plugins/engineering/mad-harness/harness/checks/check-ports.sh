#!/usr/bin/env bash
# Which of the project's declared ports are already bound, and by what.
#
#   harness/checks/check-ports.sh [--strict]     (advisory by default)
#
# Reads `ports:` from harness.yaml — declared data, never scraped from prose — and probes
# each with lsof. With none declared it says so rather than checking nothing silently.
set -euo pipefail
# Record where we were invoked from BEFORE moving: the cd below lands in the
# harness, which carries its own harness.yaml, and the resolver would read that
# as the project. Already-set wins, so a caller may state it explicitly.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec uv run python -m models.check_ports "$@"
