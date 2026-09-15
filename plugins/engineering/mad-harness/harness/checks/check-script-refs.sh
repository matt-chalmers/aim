#!/usr/bin/env bash
# Every `${CLAUDE_PLUGIN_ROOT}/...` path named in a skill, command or agent must exist.
#
#   harness/checks/check-script-refs.sh        exit 0 = every reference resolves
#
# A rename is the most ordinary edit there is, and the prose that points at the old
# name keeps reading correctly — it is simply wrong at run time. This makes it loud.
set -euo pipefail
# THIS CHECK VALIDATES PLUGIN FILES, so it resolves from its own location, the same
# orientation as check-analyst-mirror.sh. Recorded anyway so subshells stay consistent.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec uv run python -m models.check_refs "$@"
