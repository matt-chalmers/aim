#!/usr/bin/env bash
# The upgrade notes newer than a stamp, items split by their **mechanical** /
# **ask the owner** tags — what /harness-setup's upgrade opening reads in place of the page.
#
#   harness/setup/notes.sh [--after <version>] [--json]
set -euo pipefail
# Record where we were invoked from BEFORE moving: the cd below lands in the
# harness, which carries its own harness.yaml, and the resolver would read that
# as the project. Already-set wins, so a caller may state it explicitly.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.setup_notes "$@"
