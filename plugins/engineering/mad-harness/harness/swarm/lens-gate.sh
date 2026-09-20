#!/usr/bin/env bash
# /swarm step 7 and /grind §9 as one call: the brief and the L4 trigger, the suite once
# where the change is (the branch's worktree, or the primary for /grind), three or four
# lenses dispatched at once with L3 handed no diff path, unanimity, and the VERIFIED note
# on the task — written only when every lens passed. A lens that hung, was denied, was
# cut off or returned no VERDICT: line is NONE, never PASS.
#
#   harness/swarm/lens-gate.sh <task> <sha> [--branch <b>] [--lane <lane>] [--worker N]
#                              [--l4 auto|always] [--suite gate|skip] [--wave <epic>-w<n>]
#                              [--timeout S] [--dry-run] [--json]
#
# Exit 0 PASS (note written) · 1 FAIL (nothing written; the route is printed) · 2 could not judge · 3 usage.
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.lens_gate "$@"
