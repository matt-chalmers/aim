#!/usr/bin/env bash
# Validate a provider before routing any real task to it.
#
#   harness/models/probe-compat.sh deepseek
#
# Thin: the logic is an importable module. Exits non-zero unless every probe passes.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python -m models.probe_compat "$@"
