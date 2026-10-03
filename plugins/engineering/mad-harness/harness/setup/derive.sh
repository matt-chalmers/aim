#!/usr/bin/env bash
# What the repository already says about each setup block: derived values with their
# evidence, candidates shown but never written, and what only the owner can answer.
#
#   harness/setup/derive.sh [--block <id>]          all blocks, or one, as JSON
#   harness/setup/derive.sh stack-breakout --root <r> [--time-bootstrap "<cmd>"]
#
# Reads the repository and runs the stack commands' probe ladder (block 4) — writes nothing.
set -euo pipefail
# Record where we were invoked from BEFORE moving: the cd below lands in the
# harness, which carries its own harness.yaml, and the resolver would read that
# as the project. Already-set wins, so a caller may state it explicitly.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.setup_derive "$@"
