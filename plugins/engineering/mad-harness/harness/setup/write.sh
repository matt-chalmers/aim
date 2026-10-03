#!/usr/bin/env bash
# Write one setup block to harness.yaml — the only route /harness-setup uses, and only for
# a person: it refuses inside a worker or a worktree.
#
#   harness/setup/write.sh <block> [--state confirmed|declined] [--because "…"] [--remove k,…] < values.json
#   harness/setup/write.sh --render-first-run      a fresh harness.yaml from the template + derivations
#
# Exit 3 = cannot anchor the edit (the JSON carries the fragment to paste); 4 = unattended.
set -euo pipefail
# Record where we were invoked from BEFORE moving: the cd below lands in the
# harness, which carries its own harness.yaml, and the resolver would read that
# as the project. Already-set wins, so a caller may state it explicitly.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.setup_write "$@"
