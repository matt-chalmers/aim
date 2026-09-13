#!/usr/bin/env bash
# Campaign health signals as an append-only time series.
#
#   record:  harness/campaign/campaign-telemetry.sh record <epic-id> '<json payload>'
#   read:    harness/campaign/campaign-telemetry.sh
#
# A single bad epic is noise; a signal drifting across five is the finding, and that is
# invisible without history. Stored through the telemetry port, which merges whatever a
# previous backend still holds — so the series spans a backend change rather than
# restarting at it.
set -euo pipefail
# Record where we were invoked from BEFORE moving: the cd below lands in the
# harness, which carries its own harness.yaml, and the resolver would read that
# as the project. Already-set wins, so a caller may state it explicitly.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec uv run python -m tracker.campaign "$@"
