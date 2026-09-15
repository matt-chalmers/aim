#!/usr/bin/env bash
# Answer MANY search questions in ONE tool call.
#
#   harness/verify/scan.sh -e 'PATTERN' -e 'PATTERN' ... [--rev SHA] [--max N] [pathspec...]
#
# Every pattern is answered, including with zero hits — "searched and found
# nothing" is a result a lens needs, and is reported as such.
set -euo pipefail
# Record where we were invoked from BEFORE moving: the cd below lands in the
# harness, which carries its own harness.yaml, and the resolver would read that
# as the project. Already-set wins, so a caller may state it explicitly.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m verify.scan "$@"
