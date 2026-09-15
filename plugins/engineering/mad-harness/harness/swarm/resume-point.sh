#!/usr/bin/env bash
# Where a task's work already is, and at what point a resumed run should adopt it.
#
#   harness/swarm/resume-point.sh <task-id> [--json]
#
# Ask this BEFORE dispatching a task. MERGE / VERIFY / REATTACH / MERGED / FRESH — see
# models/resume.py for what each means and what to do.
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.resume "$@"
