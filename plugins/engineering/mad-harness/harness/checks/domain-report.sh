#!/usr/bin/env bash
# Where the harness's prose uses a consuming project's domain vocabulary.
# A REPORT, not a gate — see models/domain_report.py for why it cannot be a gate.
set -euo pipefail
# Record where we were invoked from BEFORE moving: the cd below lands in the
# harness, which carries its own harness.yaml, and the resolver would read that
# as the project. Already-set wins, so a caller may state it explicitly.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec uv run python -m models.domain_report "$@"
