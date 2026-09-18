#!/usr/bin/env bash
# The orchestrator card — harness/orchestrator-card.md — byte-identical in every command
# and under the card budget. `--write` copies the canonical card into every command.
#
#   harness/checks/check-orchestrator-card.sh [--write]
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.check_card "$@"
