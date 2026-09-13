#!/usr/bin/env bash
# The harness conventions block is stated in three skills so that every agent reaches it
# through something it already preloads. Three copies of one rule is exactly the shape
# that has drifted repeatedly in this repo, so they are pinned byte-identical here —
# the same idiom, and the same reasoning, as check-analyst-mirror.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python -m models.check_conventions "$@"
