#!/usr/bin/env bash
# A readable view of one epic's plan and state, for the epic's staging folder.
#
#   harness/tracker/render-epic.sh <epic-id> --write <path>
#   harness/tracker/render-epic.sh <epic-id> --check --write <path>
#
# GENERATED, and drift is a failure: the file is a VIEW, and a view somebody edits becomes
# a second source of truth. Same shape as `model:`/`effort:` and `.swarm-env`.
set -euo pipefail
# Record where we were invoked from BEFORE moving: the cd below lands in the
# harness, which carries its own harness.yaml, and the resolver would read that
# as the project. Already-set wins, so a caller may state it explicitly.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m tracker.cli render "$@"
