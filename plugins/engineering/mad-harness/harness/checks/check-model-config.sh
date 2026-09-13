#!/usr/bin/env bash
# Verify the model routing config against every agent definition.
#
# Thin on purpose: the logic is an importable Python module so the tests exercise
# it directly instead of through a subprocess. This wrapper exists so the check
# is invoked like its six siblings in this directory.
#
#   harness/checks/check-model-config.sh            report drift, exit non-zero on any
#   harness/checks/check-model-config.sh --write    regenerate model:/effort: from tiers
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python -m models.check_config "$@"
