#!/usr/bin/env bash
# campaign-loop §6's health signals for one epic, computed from the wave manifests, the
# dispatch telemetry, the epic's ADEQUACY:/AUDIT: notes and git — and, with --record, filed
# as a campaign event with the outcome the epic actually had (closed | parked | stopped),
# so a parked epic is never filed as a catastrophically bad closed one.
#
#   harness/swarm/campaign-signals.sh <epic> [--outcome closed|parked|stopped] [--mode auto|interactive] [--record] [--json]
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.campaign_signals "$@"
