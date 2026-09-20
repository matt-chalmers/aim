#!/usr/bin/env bash
# /swarm step 9 and /grind §10 as one call: close each task with its reason, then export,
# regenerate every affected epic's view, commit, pull --rebase --autostash, push — and
# `autosync on` only with --restore-autosync (a run that is the whole run; never a wave
# inside a campaign, where §5 restores it). An upstream that moved under the rebase stops
# before the push with the rest listed by hand. Exit 0 synced, 1 a step failed, 2 usage.
#
#   harness/swarm/close-wave.sh <id>[=<reason>]... [--reason "<default>"] [--message "<subject>"]
#                               [--epic ID] [--no-push] [--restore-autosync] [--check]
#   harness/swarm/close-wave.sh --sync-only [--message "<subject>"] [--epic ID] [--no-push]
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.close_wave "$@"
