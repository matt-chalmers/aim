#!/usr/bin/env bash
# A readable view of one epic's plan and state, for the epic's staging folder.
#
#   harness/tracker/render-epic.sh <epic-id> --write <path>
#   harness/tracker/render-epic.sh <epic-id> --check --write <path>
#
# GENERATED, and drift is a failure: the file is a VIEW, and a view somebody edits becomes
# a second source of truth. Same shape as `model:`/`effort:` and `.swarm-env`.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python -m tracker.cli render "$@"
