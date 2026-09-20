#!/usr/bin/env bash
# /swarm step 8 as one call: every branch a ref and the tree clean (checked BEFORE the
# slot is taken), slot-acquire, merge each FROM THE REF in ascending task-id order — a
# conflict is aborted and left unmerged with its paths named, never resolved — slot-release
# in a finally, then the whole-repo gate once on the merged result (one run.sh per stack,
# concurrently; a stack with nothing declared is not green), red attributed by git log to
# the task that touched the failing path. Suggests the revert; never performs it.
#
#   harness/swarm/merge-wave.sh <branch>... [--lane <lane> | --stack <s> ...] [--wave <epic>-w<n>] [--dry-run] [--json]
#
# Exit 0 green, no conflict · 1 gate red or any conflict · 2 a precondition failed (nothing merged).
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.merge_wave "$@"
