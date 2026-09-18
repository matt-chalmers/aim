#!/usr/bin/env bash
# campaign-loop §5 in one call. Gates first — no task blocked in prose only, the decision
# register clean with nothing open, the staging folder retired (and archived where an
# archive is declared) — then the writes: close, export, commit, pull, push, autosync on.
#
#   harness/swarm/close-epic.sh <epic> --reason "<what shipped, how verified>"
#   harness/swarm/close-epic.sh <epic> --check          # the gates only; never writes
#   harness/swarm/close-epic.sh <epic> --reason "…" --no-push   # stop after the commit
#
# A failed gate writes NOTHING (exit 1). A failed write stops there and prints what
# remains to do by hand, in order (exit 1).
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.close_epic "$@"
