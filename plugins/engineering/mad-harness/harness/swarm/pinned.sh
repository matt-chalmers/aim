#!/usr/bin/env bash
# The campaign's pinned state — claims, merge slot, worktrees, the loop's rules — read
# from disk, never from memory. The SessionStart hook prints it after a compaction, a
# resume or a fresh start whenever a campaign is in flight, and names every pinned id the
# compaction summary dropped.
#
#   harness/swarm/pinned.sh [--always] [--transcript PATH]
#   harness/swarm/pinned.sh --hook      # stdin: the hook payload
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.pinned "$@"
