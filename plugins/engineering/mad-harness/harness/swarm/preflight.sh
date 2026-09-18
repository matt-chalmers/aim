#!/usr/bin/env bash
# campaign-loop §0's gates in one call, in the loop's order: a clean tree, a config
# reviewed against the installed plugin, a free merge slot, autosync off, the declared
# ports, disk headroom. One line per step and a summary.
#
#   harness/swarm/preflight.sh          # exit 0 ready · 3 config needs /harness-setup · 1 otherwise
#
# The one write (`tk.sh autosync off`) waits for the gates before it: a pre-flight that
# fails leaves the tracker as it found it. `tk.sh memories` is not here — that is content
# the loop reads, not a gate it passes.
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.preflight "$@"
