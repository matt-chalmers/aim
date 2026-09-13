#!/usr/bin/env bash
# Probe each stack's declared commands, and repair the ones that have rotted.
#
# A wrong test command fails loudly — but LATE, and once per worker per wave, and
# a worker that gives up returns BLOCKED, which escalate.py sends to a costlier
# tier that cannot fix a config error. One ~3s probe at pre-flight replaces that.
#
#   harness/checks/check-stack-commands.sh            report only (CI-safe)
#   harness/checks/check-stack-commands.sh --repair   record a working command
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python -m models.check_commands "$@"
