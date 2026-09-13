#!/usr/bin/env bash
# Verify the skill layer: declarations resolve, names match, and no agent's
# preload bill has crept over budget.
#
#   harness/checks/check-skills.sh
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python -m models.check_skills "$@"
