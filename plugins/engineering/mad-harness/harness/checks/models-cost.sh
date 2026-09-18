#!/usr/bin/env bash
# The dispatch cost series, read back per agent and tier: cost, turns, cache rate, budget
# kills, results%, cache breaks. `make models-cost` in a consuming project runs this.
#
#   harness/checks/models-cost.sh
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.report "$@"
