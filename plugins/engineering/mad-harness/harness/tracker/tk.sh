#!/usr/bin/env bash
# The harness's tracker, whichever backend this project declares.
#
#   $HARNESS_ROOT/tracker/tk.sh ready --json
#   $HARNESS_ROOT/tracker/tk.sh claim <id> --actor swarm-w1
#   $HARNESS_ROOT/tracker/tk.sh close <id> --reason "..."
#
# Thin: the logic is an importable module so the tests exercise it directly rather than
# through a subprocess, the same shape as every other wrapper in this tree.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python -m tracker.cli "$@"
