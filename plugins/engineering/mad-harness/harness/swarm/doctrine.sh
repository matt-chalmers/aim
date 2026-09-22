#!/usr/bin/env bash
# An agent's doctrine — the skills its definition declares — as one file, for a teammate.
#
#   harness/swarm/doctrine.sh <agent>      prints .harness/run/doctrine/<agent>.md
#
# An agent-teams teammate loads its definition's tools and model but not its skills; the
# lead points it at this file. The reasoning is in models/doctrine_file.py.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.doctrine_file "$@"
