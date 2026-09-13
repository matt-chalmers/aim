#!/usr/bin/env bash
# Read MANY files or slices in ONE tool call.
#
#   harness/verify/peek.sh [--rev SHA] [--max N] SPEC [SPEC ...]
#   SPEC = path | path:START | path:START-END
#
# Every spec produces a stanza, including `!! not found` — a silently skipped
# path is one an agent reasons about without having read.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python -m verify.peek "$@"
