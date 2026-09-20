#!/usr/bin/env bash
# Stage an architect's design: design.md under the epic's staging folder (found or created
# as <bare-id>-<slug>), a draft decision record per DECISION: line with a decision task
# filed for each, and the ARCHITECTURE: pointer on the epic. plan-epic.sh does this itself;
# /design calls it after the owner accepts.
#
#   harness/swarm/stage-design.sh <epic> <the architect's result file> [--no-decisions]
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.stage_design "$@"
