#!/usr/bin/env bash
# Integrate a finished wave — through the PRODUCTION scripts: `swarm/merge-wave.sh` (the
# merges from the refs, the slot, the gate once, red attributed) then
# `swarm/close-wave.sh --sync-only` (export, the epic view, the commit), then the sweep.
#
#   harness/wavelab/merge-wave.sh [--root DIR] <beads|mdfiles>
#
# /swarm steps 8 and 9, which nothing had ever exercised until this existed. UNTIL 0.10.22
# this was its own merge loop and gate — a lab-grade copy of what /swarm step 8 had the
# orchestrator do by hand (and it skipped the slot). It calls the production modules now,
# so the lab exercises the production path and the two cannot drift.
#
# MERGE FROM THE BRANCH REF, NEVER FROM A WORKTREE — the production script's rule; the
# incident (a worktree holding a STAGED REVERT of its own fix) is in its docstring.
set -uo pipefail

ROOT="${WAVELAB_ROOT:-$HOME/harness-wavelab}"
while [ "${1:-}" = "--root" ]; do ROOT="${2:?}"; shift 2; done
NAME="${1:?usage: merge-wave.sh [--root DIR] <beads|mdfiles>}"
REPO="$ROOT/$NAME"
HARNESS="$(cd "$(dirname "$0")/.." && pwd)"

# THE LAB POINTS AT ITS TARGET BY STANDING IN IT — nothing here sets MAD_HARNESS_REPO or
# MAD_HARNESS_CALLER_PWD; the wrappers record the caller's directory themselves.
cd "$REPO" || exit 2
echo "== integrating the wave: $NAME =="
git config user.email >/dev/null 2>&1 || { git config user.email wavelab@example.com; git config user.name wavelab; }

mapfile -t BRANCHES < <(git branch --format='%(refname:short)' | grep '^harness-w' | sort || true)
[ "${#BRANCHES[@]}" -gt 0 ] || { echo "no worker branches to merge"; exit 0; }

EPIC=$("$HARNESS/tracker/tk.sh" list --type epic --json \
       | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r[0]["id"] if r else "")')
WAVE=()
if [ -n "$EPIC" ]; then
  # The wave dispatch-wave.sh opened, if it did; otherwise this integrates unrecorded.
  W=$(ls -t "$REPO"/.harness/run/waves/"$EPIC"-w*.json 2>/dev/null | head -1 || true)
  [ -n "$W" ] && WAVE=(--wave "$W")
fi

FAILED=0
"$HARNESS/swarm/merge-wave.sh" "${BRANCHES[@]}" --lane backend "${WAVE[@]}" || FAILED=$?

echo "-- tracker sync"
SYNC=(--sync-only --no-push --message "chore(tracker): sync after wave")
[ -n "$EPIC" ] && SYNC+=(--epic "$EPIC")
"$HARNESS/swarm/close-wave.sh" "${SYNC[@]}" || { echo "   sync FAILED"; FAILED=1; }

echo "-- reclaiming worktrees"
"$HARNESS/swarm/worktree-sweep.sh" --apply --min-age 0 2>&1 | tail -5

echo
echo "now ready:"
"$HARNESS/tracker/tk.sh" ready | sed 's/^/  /'
exit "$FAILED"
