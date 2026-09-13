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
# Record where we were invoked from BEFORE moving: the cd below lands in the
# harness, which carries its own harness.yaml, and the resolver would read that
# as the project. Already-set wins, so a caller may state it explicitly.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec uv run python -m models.check_config "$@"
