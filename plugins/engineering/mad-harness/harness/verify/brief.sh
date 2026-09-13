#!/usr/bin/env bash
# Precompute the shared verification brief for one task + commit.
#
#   harness/verify/brief.sh <task-id> [commit-ish] [--out DIR]
#
# Prints the path to brief.md on stdout; a size comparison on stderr.
# Hand brief.md to every lens. Hand the diff/ artefacts to L1, L2 and L4 only —
# L3 (verifier-spec) must not see the diff.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python -m verify.brief "$@"
