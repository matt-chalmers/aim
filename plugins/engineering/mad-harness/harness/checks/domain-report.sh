#!/usr/bin/env bash
# Where the harness's prose uses a consuming project's domain vocabulary.
# A REPORT, not a gate — see models/domain_report.py for why it cannot be a gate.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python -m models.domain_report "$@"
