#!/usr/bin/env bash
# The harness's tracker, whichever backend this project declares.
#
#   $HARNESS_ROOT/tracker/tk.sh ready --json
#   $HARNESS_ROOT/tracker/tk.sh claim <id> --actor swarm-w1
#   $HARNESS_ROOT/tracker/tk.sh close <id> --reason "..."
#   $HARNESS_ROOT/tracker/tk.sh note <id> --file <path>     # the note read from disk, never from the caller
#
# Thin: the logic is an importable module so the tests exercise it directly rather than
# through a subprocess, the same shape as every other wrapper in this tree.
set -euo pipefail
# Record where we were invoked from BEFORE moving: the cd below lands in the
# harness, which carries its own harness.yaml, and the resolver would read that
# as the project. Already-set wins, so a caller may state it explicitly.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m tracker.cli "$@"
