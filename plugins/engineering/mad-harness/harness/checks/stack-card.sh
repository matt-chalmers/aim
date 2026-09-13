#!/usr/bin/env bash
# Print the technology card for a lane.
#
#   harness/checks/stack-card.sh [lane]
#
# The dispatch boundary injects this automatically. This script is the fallback for
# an agent dispatched through the Agent tool, where nothing can be injected into the
# prompt — one tool call instead of a permanent preload.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python -m models.context "$@"
