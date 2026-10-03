#!/usr/bin/env bash
# Which toolchain and framework modules are in play here, at which root — and, for setup's
# agent, each candidate root's listing and direct dependencies.
#
#   harness/setup/discover.sh [--json]
#
# An enumeration over the existing classification (`Stack.present` / `Stack.ambiguous`),
# never a second detector. Works with no harness.yaml. Not a gate: it lives here rather
# than in harness/checks/, whose directory is the generated checks table.
set -euo pipefail
# Record where we were invoked from BEFORE moving: the cd below lands in the
# harness, which carries its own harness.yaml, and the resolver would read that
# as the project. Already-set wins, so a caller may state it explicitly.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.discover "$@"
