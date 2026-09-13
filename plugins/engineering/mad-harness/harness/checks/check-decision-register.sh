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
# Record where we were invoked from BEFORE moving: the cd below lands in the
# harness, which carries its own harness.yaml, and the resolver would read that
# as the project. Already-set wins, so a caller may state it explicitly.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec uv run python -m tracker.check_register "$@"
