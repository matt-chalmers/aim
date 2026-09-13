#!/usr/bin/env bash
# Answer MANY search questions in ONE tool call.
#
#   harness/verify/scan.sh -e 'PATTERN' -e 'PATTERN' ... [--rev SHA] [--max N] [pathspec...]
#
# Every pattern is answered, including with zero hits — "searched and found
# nothing" is a result a lens needs, and is reported as such.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python -m verify.scan "$@"
