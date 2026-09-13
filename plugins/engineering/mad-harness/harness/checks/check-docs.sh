#!/usr/bin/env bash
# Verify the documentation's generated parts against the code they describe.
#
# Thin on purpose: the logic is an importable Python module so the tests exercise it
# directly rather than through a subprocess.
#
#   harness/checks/check-docs.sh            report drift, exit non-zero on any
#   harness/checks/check-docs.sh --write    regenerate
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python -m models.check_docs "$@"
