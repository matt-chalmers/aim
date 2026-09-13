#!/usr/bin/env bash
# Read MANY files or slices in ONE tool call.
#
#   harness/verify/peek.sh [--rev SHA] [--max N] SPEC [SPEC ...]
#   SPEC = path | path:START | path:START-END
#
# Every spec produces a stanza, including `!! not found` — a silently skipped
# path is one an agent reasons about without having read.
set -euo pipefail
# Record where we were invoked from BEFORE moving: the cd below lands in the
# harness, which carries its own harness.yaml, and the resolver would read that
# as the project. Already-set wins, so a caller may state it explicitly.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec uv run python -m verify.peek "$@"
