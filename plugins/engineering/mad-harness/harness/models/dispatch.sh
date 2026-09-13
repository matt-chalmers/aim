#!/usr/bin/env bash
# Invoke one agent through the CLI boundary. Thin: the logic is an importable
# module so the tests exercise it directly rather than through a subprocess.
#
#   harness/models/dispatch.sh <agent> --prompt-file <path> [--tier T] [--high-risk]
#                              [--task ID] [--cwd WORKTREE] [--dry-run]
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python -m models.dispatch "$@"
