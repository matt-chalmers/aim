#!/usr/bin/env bash
# Run a stack's declared commands — many in one call.
#
# The fourth batching primitive. scan.sh, peek.sh and brief.sh batch READS, which
# are toolchain-neutral (git grep / git show / git diff). This one batches RUNS,
# so the wave gate is one call per stack rather than a composition the
# orchestrator has to remember to make.
#
# Every key you name produces a line, including one the stack does not declare —
# reported as absent, never silently dropped.
#
#   harness/verify/run.sh --stack python-uv lint typecheck test
#   harness/verify/run.sh --lane backend --scoped tests/test_x.py test_scoped
set -euo pipefail
# Record where we were invoked from BEFORE moving: the cd below lands in the
# harness, which carries its own harness.yaml, and the resolver would read that
# as the project. Already-set wins, so a caller may state it explicitly.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.commands "$@"
