#!/usr/bin/env bash
# Invoke one agent through the CLI boundary. Thin: the logic is an importable
# module so the tests exercise it directly rather than through a subprocess.
#
#   harness/models/dispatch.sh <agent> --prompt-file <path> [--tier T] [--high-risk]
#                              [--task ID] [--cwd WORKTREE] [--dry-run]
#                              [--digest [N]] [--out PATH]
#
# The whole result is written to a file and its path printed last; --digest prints only
# the first N lines (default 40) before it, so a 45k-char result costs the caller one path.
set -euo pipefail
# Record where we were invoked from BEFORE moving: the cd below lands in the
# harness, which carries its own harness.yaml, and the resolver would read that
# as the project. Already-set wins, so a caller may state it explicitly.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.dispatch "$@"
