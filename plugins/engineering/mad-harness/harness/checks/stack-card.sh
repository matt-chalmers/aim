#!/usr/bin/env bash
# Print the technology card for a lane.
#
#   harness/checks/stack-card.sh [lane]
#
# The dispatch boundary injects this automatically. This script is the fallback for
# an agent dispatched through the Agent tool, where nothing can be injected into the
# prompt — one tool call instead of a permanent preload.
set -euo pipefail
# Record where we were invoked from BEFORE moving: the cd below lands in the
# harness, which carries its own harness.yaml, and the resolver would read that
# as the project. Already-set wins, so a caller may state it explicitly.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.context "$@"
