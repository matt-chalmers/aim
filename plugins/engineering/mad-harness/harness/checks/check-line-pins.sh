#!/usr/bin/env bash
# Which records cite code by line number, and which of those pins have drifted.
#
#   harness/checks/check-line-pins.sh              # every open record
#   harness/checks/check-line-pins.sh <id>         # one record
#
# A REPORTING tool, not a gate. It cannot know whether a pin is still correct — only
# whether the file moved under it. DEAD is certain; SUSPECT needs eyes. The expensive
# failure is a pin that lands on plausible-looking WRONG text.
set -euo pipefail
# Record where we were invoked from BEFORE moving: the cd below lands in the
# harness, which carries its own harness.yaml, and the resolver would read that
# as the project. Already-set wins, so a caller may state it explicitly.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m tracker.check_pins "$@"
