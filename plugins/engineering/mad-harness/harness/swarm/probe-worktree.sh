#!/usr/bin/env bash
# Prove a worker's worktree works — /harness-setup §7's probe as one call.
#
#   harness/swarm/probe-worktree.sh [<lane>] [--worker N] [--keep]
#
# Creates a detached scratch worktree, runs swarm-worktree-init.sh inside it, sources the
# .swarm-env it wrote and checks the identity and per-worker lines, then removes the
# worktree (always, in a finally — --keep leaves it and prints the path). Exit 0 usable,
# 1 a check failed, 2 usage. The reasoning is in models/probe_worktree.py.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.probe_worktree "$@"
