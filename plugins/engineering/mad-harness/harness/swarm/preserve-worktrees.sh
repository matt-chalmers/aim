#!/usr/bin/env bash
# Preserve every worker worktree's work — uncommitted AND committed-unmerged — as files,
# before anything is removed.
#
#   harness/swarm/preserve-worktrees.sh [--to DIR]      default DIR = .harness/halted-<UTC date>
#
# /halt said "never discard uncommitted work without showing it first" and offered no
# action for keeping it; a consuming repository did this by hand after an incident. For
# each worker worktree that holds anything: the uncommitted diff (staged and unstaged),
# the list of untracked files with their contents, and one patch per commit ahead of the
# default branch — under <DIR>/<branch>/. Prints a line per worktree naming the folder, so
# the path can be written on the task. Read-only against the worktrees themselves.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
MAIN="${MAIN_BRANCH:-}"
if [ -z "$MAIN" ]; then
  MAIN="$( { git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true; } | sed 's|^origin/||')"
fi
if [ -z "$MAIN" ]; then
  for c in main master trunk; do
    if git rev-parse --verify --quiet "$c" >/dev/null; then MAIN="$c"; break; fi
  done
fi
[ -n "$MAIN" ] || { echo "cannot determine the default branch; set MAIN_BRANCH" >&2; exit 2; }
TO=".harness/halted-$(date -u +%Y-%m-%d)"
while [ $# -gt 0 ]; do case "$1" in --to) TO="${2:?}"; shift ;; *) echo "unknown argument: $1" >&2; exit 2 ;; esac; shift; done
PRIMARY="$(git rev-parse --show-toplevel)"
n=0
while IFS= read -r wt; do
  [ -n "$wt" ] && [ "$wt" != "$PRIMARY" ] && [ -d "$wt" ] || continue
  branch="$(git -C "$wt" symbolic-ref --quiet --short HEAD 2>/dev/null || echo "detached-$(git -C "$wt" rev-parse --short HEAD)")"
  dirty="$(git -C "$wt" status --porcelain 2>/dev/null | grep -v '^?? \.swarm' || true)"
  ahead="$(git -C "$wt" rev-list --count "$MAIN..HEAD" 2>/dev/null || echo 0)"
  [ -n "$dirty" ] || [ "$ahead" -gt 0 ] || continue
  dest="$TO/$branch"; mkdir -p "$dest"
  git -C "$wt" diff > "$dest/unstaged.diff"
  git -C "$wt" diff --cached > "$dest/staged.diff"
  git -C "$wt" status --porcelain > "$dest/status.txt"
  while IFS= read -r -d '' f; do
    case "$f" in .swarm*|*/.swarm*) continue ;; esac   # harness residue is not work
    mkdir -p "$PRIMARY/$dest/untracked/$(dirname "$f")"
    cp "$wt/$f" "$PRIMARY/$dest/untracked/$f"
  done < <(git -C "$wt" ls-files --others --exclude-standard -z)
  if [ "$ahead" -gt 0 ]; then
    git -C "$wt" format-patch -q -o "$PRIMARY/$dest/commits" "$MAIN..HEAD" >/dev/null
  fi
  printf '%s\n' "worktree: $wt" "branch: $branch" "ahead of $MAIN: $ahead commit(s)" "preserved: $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$dest/README.txt"
  echo "  preserved $branch → $dest  (uncommitted: $([ -n "$dirty" ] && echo yes || echo no), commits ahead: $ahead)"
  n=$((n+1))
done < <(git worktree list --porcelain | awk '/^worktree /{print substr($0,10)}')
if [ "$n" = "0" ]; then echo "  nothing to preserve — no worker worktree holds uncommitted or unmerged work"; else
echo "  $n worktree(s) preserved under $TO. Note the path on each task before removing anything."; fi
