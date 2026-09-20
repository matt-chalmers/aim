#!/usr/bin/env bash
# /swarm step 10's four health signals and the 8b accretion check, computed from the wave manifest and git, with direction against the previous wave — and the telemetry payload ready to record.
#
#   harness/swarm/wave-report.sh <epic>-w<n> | <manifest path> [--json]
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.wave_report "$@"
