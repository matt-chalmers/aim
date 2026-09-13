#!/usr/bin/env bash
# Verify harness.yaml + harness/stacks/, and that the places still
# hard-coding those facts agree with them.
#
#   harness/checks/check-project-config.sh
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python -m models.check_project "$@"
