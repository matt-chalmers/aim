#!/usr/bin/env bash
# Read an A/B series back: per arm, the distribution of every per-dispatch metric that
# a lever can move, and the delta between arms.
#
#   harness/wavelab/ab-report.sh <lever> [--root DIR]
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.ab_report "$@"
