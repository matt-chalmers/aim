#!/usr/bin/env bash
# campaign-loop §1 and §2 as one call: every open epic ordered P0→P3 then oldest, each
# EXCLUDED with its reason (a gate holds it, a PARKED note, a lease another machine holds)
# or triaged UNPLANNED / PARTIAL / READY with its dispatchable-on-entry count (signal ⑤).
#
#   harness/swarm/epic-queue.sh [--json] [--no-leases]
#
# Exit 0 at least one runnable epic · 1 none · 2 the tracker could not answer.
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.epic_queue "$@"
