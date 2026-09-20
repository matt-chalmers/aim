#!/usr/bin/env bash
# A worker's commit as one call: one task, one commit, inside the merge slot. The index is
# checked for paths you did not name BEFORE the slot is taken (a contaminated index is a
# refusal, not a mutex holder), the tracker's export is refused, your paths are staged
# explicitly — never -A, never . — the commit is made, and the slot is released in a finally.
#
#   harness/swarm/commit.sh <task> -m "<type>(<scope>): … (<task>)" -- <every path you changed>
#
# Exit 0 committed · 1 refused (contaminated index, the export, nothing to commit, the slot held) · 2 usage.
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.commit "$@"
