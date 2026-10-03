#!/usr/bin/env bash
# Where /harness-setup stands: the opening (first run, upgrade, revisit, repair, re-stamp,
# refuse), the owed blocks with why, and the upgrade notes for the agent to map.
#
#   harness/setup/state.sh [revisit | repair <block>]
#
# Computed from the config, the ledger, the stamp and discovery — never stored.
set -euo pipefail
# Record where we were invoked from BEFORE moving: the cd below lands in the
# harness, which carries its own harness.yaml, and the resolver would read that
# as the project. Already-set wins, so a caller may state it explicitly.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.setup_state "$@"
