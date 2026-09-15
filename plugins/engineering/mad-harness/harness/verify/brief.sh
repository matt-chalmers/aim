#!/usr/bin/env bash
# Precompute the shared verification brief for one task + commit.
#
#   harness/verify/brief.sh <task-id> [commit-ish] [--out DIR]
#
# Prints the path to brief.md on stdout; a size comparison on stderr.
# Hand brief.md to every lens. Hand the diff/ artefacts to L1, L2 and L4 only —
# L3 (verifier-spec) must not see the diff.
set -euo pipefail
# Record where we were invoked from BEFORE moving: the cd below lands in the
# harness, which carries its own harness.yaml, and the resolver would read that
# as the project. Already-set wins, so a caller may state it explicitly.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m verify.brief "$@"
