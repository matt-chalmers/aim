#!/usr/bin/env bash
# Integrate a finished wave: merge each branch, run the whole-repo gate, sync the tracker.
#
#   harness/wavelab/merge-wave.sh [--root DIR] <beads|mdfiles>
#
# /swarm steps 8 and 9, which nothing had ever exercised. Workers commit on branches and
# until this runs, nothing has ever combined them — so the wave gate, the merge slot and
# the tracker sync were all untested, and wave 2 cannot build on wave 1 without it.
#
# MERGE FROM THE BRANCH REF, NEVER FROM A WORKTREE. A worktree for a task under
# remediation was once found holding a STAGED REVERT of its own fix while `git log` on the
# branch showed the good commit; committing from that directory would have silently undone
# the work.
set -uo pipefail

ROOT="${WAVELAB_ROOT:-$HOME/harness-wavelab}"
while [ "${1:-}" = "--root" ]; do ROOT="${2:?}"; shift 2; done
NAME="${1:?usage: merge-wave.sh [--root DIR] <beads|mdfiles>}"
REPO="$ROOT/$NAME"
HARNESS="$(cd "$(dirname "$0")/.." && pwd)"

# THE LAB POINTS AT ITS TARGET BY STANDING IN IT. It used to export MAD_HARNESS_REPO,
# which every wrapper — and every agent dispatch.py spawned, since it inherits the
# environment — then honoured ahead of resolving anything. So the one question a real
# project needs answered ("which repository?") was answered for it, and a resolver that
# returned the plugin's own directory passed every wave here while a real project got
# an empty backlog with exit 0. Nothing in this lab sets MAD_HARNESS_REPO or
# MAD_HARNESS_CALLER_PWD: the wrappers record the caller's directory themselves.
cd "$REPO" || exit 2
echo "== integrating the wave: $NAME =="

BRANCHES=$(git branch --format='%(refname:short)' | grep '^harness-w' || true)
[ -n "$BRANCHES" ] || { echo "no worker branches to merge"; exit 0; }

# ASCENDING ORDER, so a conflict is attributable. A wave with a merge conflict is a
# planning miss by definition — the contention matrix should have serialised them — so the
# right response is to stop and say which two, not to resolve it here.
FAILED=0
for b in $(printf '%s\n' "$BRANCHES" | sort); do
  printf -- '-- merging %s ... ' "$b"
  if git merge --no-edit --no-ff "$b" >/dev/null 2>&1; then
    echo "ok"
  else
    echo "CONFLICT"
    git merge --abort 2>/dev/null
    echo "   a conflict here is a step-3 planning miss: these two tasks should not have"
    echo "   shared a wave. Left unmerged rather than resolved."
    FAILED=$((FAILED+1))
  fi
done

echo "-- whole-repo wave gate"
if "$HARNESS/verify/run.sh" --lane backend test 2>&1 | tail -3; then
  echo "   gate green"
else
  echo "   GATE RED — the wave does not land"; FAILED=$((FAILED+1))
fi

echo "-- tracker sync"
"$HARNESS/tracker/tk.sh" export
EPIC=$("$HARNESS/tracker/tk.sh" list --type epic --json \
       | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r[0]["id"] if r else "")')
if [ -n "$EPIC" ]; then
  VIEW=$(ls -d "$REPO"/docs/proposed/"$EPIC"* 2>/dev/null | head -1)
  [ -n "$VIEW" ] && "$HARNESS/tracker/render-epic.sh" "$EPIC" --write "$VIEW/tasks.md"
fi
git add -A && git -c user.email=w@e -c user.name=w \
  commit -q -m "chore(tracker): sync after wave" 2>/dev/null

echo "-- reclaiming worktrees"
"$HARNESS/swarm/worktree-sweep.sh" --apply --min-age 0 2>&1 | tail -5

echo
echo "now ready:"
"$HARNESS/tracker/tk.sh" ready | sed 's/^/  /'
exit "$FAILED"
