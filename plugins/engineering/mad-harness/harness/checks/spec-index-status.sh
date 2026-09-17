#!/usr/bin/env bash
# Answer campaign-loop §3a's reuse / delta / rebuild decision mechanically, and move the
# index's baseline after a DELTA survey has landed.
#
#   harness/checks/spec-index-status.sh <epic-id>                       REUSE | DELTA | REBUILD
#   harness/checks/spec-index-status.sh <epic-id> --stamp [--cite <path>]...
#
# Either id form — bare or prefixed — is accepted. See models/spec_index.py.
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.spec_index "$@"
