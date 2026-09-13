#!/usr/bin/env bash
# Retire a spent epic's staging folder by MOVING it into paths.archive, stamped and dated.
#
#   harness/checks/archive-epic.sh <epic-id>
#
# Fold-in ② deletes today. Archiving instead makes the close-out gate real: "the staging
# folder is empty" passes for an epic that folded nothing in, whereas "empty AND the
# archive entry exists" does not.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python -m tracker.archive "$@"
