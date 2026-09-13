#!/usr/bin/env bash
# Verify harness.yaml + harness/stacks/, and that the places still
# hard-coding those facts agree with them.
#
#   harness/checks/check-project-config.sh
set -euo pipefail
# Record where we were invoked from BEFORE moving: the cd below lands in the
# harness, which carries its own harness.yaml, and the resolver would read that
# as the project. Already-set wins, so a caller may state it explicitly.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec uv run python -m models.check_project "$@"
