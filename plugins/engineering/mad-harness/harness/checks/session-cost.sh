#!/usr/bin/env bash
# What one session cost in context, read from its transcript: requests, context growth, the
# jumps and what caused them, the largest injected texts and tool results. Tokens, not
# dollars. The orchestrator-side measurement — the field analysis this replaces was $69 of
# hand work over the same rows.
#
#   harness/checks/session-cost.sh <transcript.jsonl | session-id> [--json] [--top N] [--jump TOKENS]
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.session_cost "$@"
