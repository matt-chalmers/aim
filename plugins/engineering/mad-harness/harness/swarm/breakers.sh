#!/usr/bin/env bash
# campaign-loop §4.5's circuit breakers, checked over the wave manifests: each trip printed with the table's prescribed action. Exit 0 clear · 1 tripped (your call) · 2 hard park · 3 stop the run.
#
#   harness/swarm/breakers.sh <epic> [--max-waves 6] [--json]
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.breakers "$@"
