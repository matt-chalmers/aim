#!/usr/bin/env bash
# Verify the skill layer: declarations resolve, names match, and no agent's
# preload bill has crept over budget.
#
#   harness/checks/check-skills.sh
set -euo pipefail
# Record where we were invoked from BEFORE moving: the cd below lands in the
# harness, which carries its own harness.yaml, and the resolver would read that
# as the project. Already-set wins, so a caller may state it explicitly.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec uv run python -m models.check_skills "$@"
