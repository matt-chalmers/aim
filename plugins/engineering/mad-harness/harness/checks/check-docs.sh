#!/usr/bin/env bash
# Verify the documentation's generated parts against the code they describe.
#
# Thin on purpose: the logic is an importable Python module so the tests exercise it
# directly rather than through a subprocess.
#
#   harness/checks/check-docs.sh            report drift, exit non-zero on any
#   harness/checks/check-docs.sh --write    regenerate
set -euo pipefail
# Record where we were invoked from BEFORE moving: the cd below lands in the
# harness, which carries its own harness.yaml, and the resolver would read that
# as the project. Already-set wins, so a caller may state it explicitly.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.check_docs "$@"
