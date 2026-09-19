#!/usr/bin/env bash
# /campaign-auto as one fresh headless orchestrator session per epic: pre-flight once,
# then for each open epic dispatch campaign-orchestrator through the boundary and read
# its digest. Exit 5 when the account's usage window closes.
#
#   harness/swarm/campaign.sh [--max-epics N] [--epic ID] [--skip-preflight]
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.campaign_auto "$@"
