#!/usr/bin/env bash
# Validate a provider before routing any real task to it.
#
#   harness/models/probe-compat.sh deepseek
#
# Thin: the logic is an importable module. Exits non-zero unless every probe passes.
set -euo pipefail
# Record where we were invoked from BEFORE moving: the cd below lands in the
# harness, which carries its own harness.yaml, and the resolver would read that
# as the project. Already-set wins, so a caller may state it explicitly.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.probe_compat "$@"
