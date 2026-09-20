#!/usr/bin/env bash
# /halt as one call per form. `assess` reads and prints everything a halt looks at — the
# main tree, every worktree's uncommitted and unmerged state, the claims (with liveness),
# the merge slot, the autosync state, the open waves — and changes nothing. `pause <epic>`
# keeps the claims (a claimed task is already out of `ready`) and parks the epic.
# `release [<task>…]` hands them back: preserve every worktree's work to files FIRST (a
# failure stops everything), then per task the resume point, `tk.sh release --force`, a
# note naming where the work is, and — only with --drop-uncommitted — the worktree removed.
# Both writes end with the slot (released only when its holder is provably gone), prune,
# export, commit, push and `autosync on`.
#
#   harness/swarm/halt.sh assess
#   harness/swarm/halt.sh pause <epic> [--no-push] [--dry-run]
#   harness/swarm/halt.sh release [<task>…] [--epic ID] [--keep-uncommitted|--drop-uncommitted] [--no-push] [--dry-run]
#
# Exit 0 · 1 a write failed (the line says what remains) · 2 usage, or preserve failed (nothing released).
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.halt "$@"
