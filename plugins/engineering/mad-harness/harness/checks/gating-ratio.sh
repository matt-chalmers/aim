#!/usr/bin/env bash
# How much of the backlog is buildable at all — open records, how many are decisions or
# requirement gaps, and how many human gates are holding epics.
#
#   harness/checks/gating-ratio.sh
#
# WHY THE NUMBER MATTERS: a campaign that lands one task per epic is not an inefficient
# campaign, it is a campaign running against a gated queue — and the fix for that is
# answering decisions, not tuning the pipeline.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python -m tracker.check_gating "$@"
