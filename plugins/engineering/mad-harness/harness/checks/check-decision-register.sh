#!/usr/bin/env bash
# Verify a staged epic's decision register against the tracker, and check cross-proposal
# contention.
#
#   harness/checks/check-decision-register.sh [<epic-id>]     (no arg = every register)
#
# The register owns the epic<->decision association; the tracker owns status. An epic with
# no register did not pass the decision gate — the check says so rather than exiting
# silently, because silence reads as "checked and clean" when it means "never checked".
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python -m tracker.check_register "$@"
