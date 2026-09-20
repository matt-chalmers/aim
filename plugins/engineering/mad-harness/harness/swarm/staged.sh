#!/usr/bin/env bash
# The staged-folder edits spec-editor makes at fold-in ① and ②, line-anchored, one call each.
#
#   harness/swarm/staged.sh section <file> "<heading>"                 body under the heading; exit 1 absent, 2 duplicated
#   harness/swarm/staged.sh set-status <file> folded-in [k=v ...]      frontmatter in place, folded_in dated
#   harness/swarm/staged.sh promote-adr <draft> --decision "<text>"    git mv to paths.adrs at adr-next; prints the path
#
# The reasoning is in models/staged.py.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.staged "$@"
