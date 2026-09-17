#!/usr/bin/env bash
# worktree-sweep.sh — reclaim agent worktrees without ever destroying work.
#
#   harness/swarm/worktree-sweep.sh                 # dry run: say what would happen
#   harness/swarm/worktree-sweep.sh --apply         # actually remove
#   harness/swarm/worktree-sweep.sh --apply --min-age 0   # skip the liveness guard (see below)
#   harness/swarm/worktree-sweep.sh --apply --prune-orphans   # also delete orphaned refs whose tasks are all closed
#
# WHY THIS EXISTS
# ---------------
# `git worktree prune` only forgets worktrees whose DIRECTORY IS ALREADY GONE. It is a no-op
# for the ones that accumulate — one per dispatched task, for a whole run. A campaign reached
# 35 before this was noticed.
#
# THE RULE: THE BRANCH REF IS THE AUTHORITY, NOT THE WORKTREE. A committed worktree on a named
# branch is redundant with its ref — `git archive <branch>` reproduces it — so the directory
# can go. ANYTHING NOT REPRESENTED BY A REF IS NOT REDUNDANT AND IS NEVER REMOVED.
#
# THREE INCIDENTS SHAPED THIS, all 2026-08-27:
#  1. A worktree for a task under remediation held a STAGED REVERT of the fix (7 insertions,
#     261 deletions, five tests removed by name) while `git log` on the branch still showed the
#     good commit. Committing from that directory would have silently undone the work.
#     => Merge from the branch ref, never from a worktree you did not just create.
#  2. One worktree held the ONLY copy of an uncommitted change. A blind `remove --force` loop
#     would have destroyed it. => DIRTY IS REPORTED, NEVER REMOVED.
#  3. The FIRST version of this script checked dirtiness with `--untracked-files=no` while
#     removing with `--force`, so a worktree whose only work was NEW, NEVER-ADDED FILES read as
#     "clean" and was deleted — reintroducing incident 2 inside the script written to prevent
#     it. Untracked files now count as dirty, except a known-residue allowlist.
#
# CLASSIFICATION
#   merged    branch merged into main   -> remove worktree AND delete branch (`-d`, never `-D`)
#   clean     committed, branch unmerged-> remove worktree, KEEP the branch ref
#   DIRTY     any uncommitted work,     -> REPORT ONLY. Never removed, whatever flag you pass.
#             including untracked files
#   DETACHED  no branch ref at all      -> REPORT ONLY. Its commits are reachable from nothing.
#   LIVE      touched within --min-age  -> SKIPPED. An agent may still be working in it.
set -euo pipefail

APPLY=0; MIN_AGE=30; PRUNE_ORPHANS=0
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TK="$HERE/../tracker/tk.sh"
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --prune-orphans) PRUNE_ORPHANS=1 ;;
    --min-age) MIN_AGE="${2:?--min-age needs minutes}"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
# THE DEFAULT BRANCH IS ASKED FOR, NOT ASSUMED. Hardcoding `main` made the sweep die with
# "fatal: malformed object name main" on any repository that calls it something else — and
# a sweep that dies leaves every worktree behind, which is the state it exists to prevent.
MAIN="${MAIN_BRANCH:-}"
if [ -z "$MAIN" ]; then
  MAIN=$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||')
fi
if [ -z "$MAIN" ]; then
  for candidate in main master trunk; do
    git rev-parse --verify --quiet "$candidate" >/dev/null && { MAIN="$candidate"; break; }
  done
fi
if [ -z "$MAIN" ]; then
  echo "cannot determine the default branch; set MAIN_BRANCH" >&2; exit 2
fi
cd "$(git rev-parse --show-toplevel)"
PRIMARY="$(git rev-parse --show-toplevel)"
MERGED_SET="$(git branch --merged "$MAIN" --format='%(refname:short)')"

# Harness residue that does not constitute work. Everything else untracked counts as DIRTY.
# HARNESS RESIDUE IS A NAMESPACE, NOT A LIST. This was `.swarm-env` alone, and a worktree
# holding `.swarm-pytest.env` or `.swarm/commitmsg.txt` — worker scratch an EARLIER harness
# version wrote — read as DIRTY, which this script refuses to remove, correctly and forever.
# Two of the three stranded worktrees behind the 35-worktree incident were held by nothing
# else. Worktrees outlive upgrades, so the pattern has to cover every artefact the harness
# has ever written, which an enumerated list cannot. Everything the harness writes into a
# worktree starts `.swarm`; no source file does. A TRACKED change is still dirt regardless.
is_residue() { case "$1" in .swarm|.swarm/|.swarm/*|.swarm-*|*.pyc|__pycache__/*|.venv/*|node_modules/*) return 0;; *) return 1;; esac; }

n_merged=0; n_clean=0; n_dirty=0; n_detached=0; n_live=0
DIRTY_REPORT=(); DETACHED_REPORT=(); LIVE_REPORT=()

# bash 3.2 on macOS has no `mapfile`.
while IFS= read -r p; do
  [ -n "$p" ] || continue
  [ "$p" = "$PRIMARY" ] && continue
  case "$p" in */.claude/worktrees/*|/tmp/*|/private/tmp/*) ;; *) continue;; esac
  [ -d "$p" ] || continue

  # LIVENESS: an agent may still be working here. Checked before anything else.
  if [ "$MIN_AGE" -gt 0 ] && [ -n "$(find "$p" -maxdepth 2 -newermt "-${MIN_AGE} minutes" -print -quit 2>/dev/null)" ]; then
    n_live=$((n_live+1)); LIVE_REPORT+=("$p"); continue
  fi

  branch="$(git -C "$p" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"

  # DIRTY: tracked changes OR any non-residue untracked file. (Incident 3.)
  dirty="$(git -C "$p" status --porcelain 2>/dev/null || true)"
  real_dirt=""
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    f="${line:3}"
    if [ "${line:0:2}" = "??" ]; then is_residue "$f" && continue; fi
    real_dirt="${real_dirt}${line}
"
  done <<< "$dirty"

  if [ -n "$real_dirt" ]; then
    n_dirty=$((n_dirty+1))
    DIRTY_REPORT+=("${branch:-(detached)}|$p|$(printf '%s' "$real_dirt" | head -3 | tr '\n' ';' || true)")
    continue
  fi

  # DETACHED: clean, but no ref holds these commits. Removing them loses them.
  if [ -z "$branch" ]; then
    n_detached=$((n_detached+1))
    DETACHED_REPORT+=("$p|$(git -C "$p" rev-parse --short HEAD 2>/dev/null || echo '?')")
    continue
  fi

  # -F: a task-id branch name contains dots, which are regex metacharacters.
  if printf '%s\n' "$MERGED_SET" | grep -Fqx "$branch"; then
    n_merged=$((n_merged+1))
    if [ "$APPLY" = "1" ]; then
      git worktree remove --force "$p"
      git branch -d "$branch" >/dev/null 2>&1 || true
    else echo "  would remove (merged, branch deleted too): $branch"; fi
  else
    n_clean=$((n_clean+1))
    if [ "$APPLY" = "1" ]; then
      git worktree remove --force "$p"
    else echo "  would remove (clean; branch ref KEPT):     $branch"; fi
  fi
done < <(git worktree list --porcelain | awk '/^worktree /{print substr($0,10)}')

[ "$APPLY" = "1" ] && git worktree prune

# ---------------------------------------------------------------------------------------
# PASS 2 — REFS WITHOUT A DIRECTORY. Everything above hangs off `git worktree list`, so a
# worker branch whose directory is gone is invisible to it: removed by hand, lost with
# /tmp, or — the common case — reclaimed by THIS script's own "committed, unmerged: remove
# the worktree, keep the ref" path, which is right in isolation and moves the branch
# outside the only enumeration that could later see it. Every sweep after that reports
# clean. A campaign pre-flight found 70 such refs, 56 unmerged, holding real tested work
# for tasks that were then re-dispatched from scratch.
#
# Two naming schemes: the harness's own SDK dispatch (`harness-w<n>-<slug>`) and Claude
# Code's worktree isolation (`worktree-agent-*`), which the Agent tool uses.
#
# A ref carries no task id, so its task is read from its commit messages — the worker
# protocol requires the id there — and the tracker says whether that task is still open.
# ---------------------------------------------------------------------------------------
LIVE_BRANCHES="$(git worktree list --porcelain | awk '/^branch /{sub("refs/heads/","",$2); print $2}')"
# One tracker call for every status. Read-only; an unreachable tracker degrades every
# unmerged orphan to UNKNOWN rather than to "fine".
TRACKER_OUT="$("$TK" --readonly list --json 2>/dev/null | python3 -c '
import json, re, sys
try:
    rows = json.load(sys.stdin)
except Exception:
    rows = []
ids = [str(r.get("id", "")) for r in rows if r.get("id")]
prefixes = sorted({i.rsplit("-", 1)[0] for i in ids if "-" in i})
print("(" + "|".join(re.escape(p) for p in prefixes) + r")-[A-Za-z0-9]+(\.[0-9]+)*" if prefixes else "")
for r in rows:
    print(r.get("id", ""), r.get("status", ""))
' 2>/dev/null || true)"
ID_RE="$(printf '%s\n' "$TRACKER_OUT" | head -1)"
STATUS_MAP="$(printf '%s\n' "$TRACKER_OUT" | tail -n +2)"
n_orph_merged=0; n_orph_flight=0; n_orph_stale=0; n_orph_unknown=0; n_orph_live=0
FLIGHT_REPORT=(); STALE_REPORT=(); UNKNOWN_REPORT=()
now=$(date +%s)
while IFS=' ' read -r ref cdate; do
  [ -n "$ref" ] || continue
  printf '%s\n' "$LIVE_BRANCHES" | grep -Fqx "$ref" && continue
  if [ "$MIN_AGE" -gt 0 ] && [ $((now - cdate)) -lt $((MIN_AGE * 60)) ]; then
    n_orph_live=$((n_orph_live+1)); continue
  fi
  if git merge-base --is-ancestor "$ref" "$MAIN" 2>/dev/null; then
    n_orph_merged=$((n_orph_merged+1))
    if [ "$APPLY" = "1" ]; then git branch -d "$ref" >/dev/null 2>&1 || true
    else echo "  would delete (nothing ahead of main — merged or empty; no worktree): $ref"; fi
    continue
  fi
  ids=""
  if [ -n "$ID_RE" ]; then
    ids="$(git log --format='%s%n%b' "$MAIN..$ref" 2>/dev/null | grep -oE "$ID_RE" | sort -u || true)"
  fi
  open=""; closed=""
  for id in $ids; do
    st="$(printf '%s\n' "$STATUS_MAP" | awk -v id="$id" '$1==id{print $2; exit}')"
    case "$st" in closed) closed="$closed $id";; "") ;; *) open="$open $id";; esac
  done
  if [ -z "$open" ] && [ -z "$closed" ]; then
    n_orph_unknown=$((n_orph_unknown+1)); UNKNOWN_REPORT+=("$ref"); continue
  fi
  if [ -n "$open" ]; then
    n_orph_flight=$((n_orph_flight+1)); FLIGHT_REPORT+=("$ref|${open# }"); continue
  fi
  n_orph_stale=$((n_orph_stale+1))
  if [ "$APPLY" = "1" ] && [ "$PRUNE_ORPHANS" = "1" ]; then
    git branch -D "$ref" >/dev/null 2>&1 || true
  else
    STALE_REPORT+=("$ref|${closed# }")
  fi
done < <(git for-each-ref --format='%(refname:short) %(committerdate:unix)' 'refs/heads/harness-w*' 'refs/heads/worktree-agent-*')

echo
echo "  merged worktrees + branches removed : $n_merged"
echo "  clean worktrees removed, refs kept  : $n_clean"
echo "  DIRTY, left alone                   : $n_dirty"
echo "  DETACHED, left alone                : $n_detached"
echo "  LIVE (touched <${MIN_AGE}m), skipped       : $n_live"
echo
echo "  ORPHANED REFS — worker branches no worktree points at:"
echo "    merged, branch deleted            : $n_orph_merged"
echo "    IN FLIGHT (task still open)       : $n_orph_flight"
echo "    STALE (every task closed)         : $n_orph_stale"
echo "    UNKNOWN (no task id in its log)   : $n_orph_unknown"
echo "    LIVE (committed <${MIN_AGE}m), skipped  : $n_orph_live"
if [ "$n_orph_flight" -gt 0 ]; then
  echo
  echo "  IN FLIGHT — committed work for OPEN tasks that no worktree holds. Nobody is working"
  echo "  on these; re-dispatching the task starts from scratch. Adopt each one — merge it, or"
  echo "  dispatch the task FROM this branch — before the task is dispatched again:"
  for d in "${FLIGHT_REPORT[@]}"; do IFS='|' read -r r i <<< "$d"; echo "    $r  ($i)"; done
fi
if [ "$n_orph_stale" -gt 0 ]; then
  echo
  echo "  STALE — every task in the log is closed, so the work landed some other way or was"
  echo "  abandoned. Kept unless you pass --apply --prune-orphans:"
  for d in "${STALE_REPORT[@]}"; do IFS='|' read -r r i <<< "$d"; echo "    $r  ($i)"; done
fi
if [ "$n_orph_unknown" -gt 0 ]; then
  echo
  echo "  UNKNOWN — no task id in the commit messages, or the tracker could not be read."
  echo "  Kept; read the log before deciding:"
  for r in "${UNKNOWN_REPORT[@]}"; do echo "    $r"; done
fi

if [ "$n_dirty" -gt 0 ]; then
  echo
  echo "  UNCOMMITTED WORK — exists HERE AND NOWHERE ELSE. A staged diff may be a REVERT of"
  echo "  good work rather than new work (incident 1), so read it before acting:"
  for d in "${DIRTY_REPORT[@]}"; do
    IFS='|' read -r b p s <<< "$d"; echo "    $b"; echo "      $p"; echo "      $s"
  done
fi
if [ "$n_detached" -gt 0 ]; then
  echo
  echo "  DETACHED HEAD — no branch ref points at these commits. Give them a branch"
  echo "  (git -C <path> switch -c <name>) or confirm they are disposable, then re-run:"
  for d in "${DETACHED_REPORT[@]}"; do IFS='|' read -r p h <<< "$d"; echo "    $h  $p"; done
fi
[ "$APPLY" = "1" ] || echo "
  dry run — nothing changed. Re-run with --apply."
exit 0
