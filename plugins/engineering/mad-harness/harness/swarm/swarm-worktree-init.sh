#!/usr/bin/env bash
# Bootstrap an isolated worktree for one swarm worker.
#
#   harness/swarm/swarm-worktree-init.sh <worker-n> [lane]
#
# Run this FROM INSIDE the worktree. It refuses in the primary checkout.
#
# Thin on purpose. What a worker needs — which dependency dirs to restore, how to
# restore each, and what environment to export — is declared by the stack modules
# under harness/stacks/ and assembled by harness/models/worker.py. Typing those
# values here is what let the DB_NAME invariant break three times: the prose was
# corrected twice while this script still contradicted it.
set -euo pipefail

# Record where we were invoked from. This script reaches into the harness via a
# subshell or a computed path, so Python still runs with the harness as its cwd —
# and the harness carries its own harness.yaml, which the resolver would read as
# the project. Exported here so every subshell inherits it.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"

# Capture the worktree BEFORE cd-ing to the module root. Reading it back from
# Python's cwd would yield harness/, not the worktree — which silently bootstraps
# and writes .swarm-env one directory deep, where nothing looks for it.
WT="$(pwd)"
cd "$(dirname "${BASH_SOURCE[0]}")/.."
exec env -u VIRTUAL_ENV uv run python -m models.worker --worktree "$WT" "$@"
