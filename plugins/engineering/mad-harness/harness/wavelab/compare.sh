#!/usr/bin/env bash
# Diff what the two backends produced from identical work.
#
#   harness/wavelab/compare.sh [--root DIR]
#
# THE POINT OF THE WHOLE LAB. Either backend passing its own tests proves it is
# self-consistent; the conformance contract proves they agree on every rule someone
# thought to write down. This asks the question neither can: given the same base, the same
# epic and the same dispatched work, did they end up in the same place?
#
# Compares OUTCOMES, never ids or timestamps — those differ by construction.
set -uo pipefail
ROOT="${WAVELAB_ROOT:-$HOME/harness-wavelab}"
while [ "${1:-}" = "--root" ]; do ROOT="${2:?}"; shift 2; done
HARNESS="$(cd "$(dirname "$0")/.." && pwd)"
# THE LAB POINTS AT ITS TARGET BY STANDING IN IT. It used to export MAD_HARNESS_REPO,
# which every wrapper — and every agent dispatch.py spawned, since it inherits the
# environment — then honoured ahead of resolving anything. So the one question a real
# project needs answered ("which repository?") was answered for it, and a resolver that
# returned the plugin's own directory passed every wave here while a real project got
# an empty backlog with exit 0. Nothing in this lab sets MAD_HARNESS_REPO or
# MAD_HARNESS_CALLER_PWD: the wrappers record the caller's directory themselves.
tk() { ( cd "$1" && shift && "$HARNESS/tracker/tk.sh" "$@" ); }   # $1 = repo, then verbs

facts() {  # $1 = repo name -> normalised, id-free facts about the outcome
  local repo="$ROOT/$1"
  # Keyed by TITLE, never by id: the two backends generate different id schemes by
  # construction, and comparing those would report a difference on every line.
  tk "$repo" list --json | python3 -c '
import json, sys
rows = json.load(sys.stdin)
# PERMISSION REQUESTS ARE NOT AN OUTCOME. One backend filing one and the other not is
# agent behaviour varying between runs, not the backends disagreeing — and reporting it as
# divergence is how this check starts crying wolf again.
# AND THE EDGES THEY CREATE, not just the records. A filed request blocks its task, so a
# run where an agent asked a question shows a different dependency count — which is the
# same behavioural variance, arriving through the graph instead of the record list.
perms = {r["id"] for r in rows if r["title"].startswith("Permission:")}
rows = [r for r in rows if r["id"] not in perms]
for r in sorted(rows, key=lambda x: x["title"]):
    deps = len([d for d in r["depends_on"] if d not in perms])
    print("task   %-8s %-10s deps=%d  %s" % (r["type"], r["status"], deps, r["title"]))
'
  ( cd "$repo" && git branch --format='%(refname:short)' | grep -c harness-w \
      | xargs -I{} echo "branches {}" )
  # SKIP WHAT THE TRACKER ITSELF OWNS, and ask the backend which paths those are rather
  # than hardcoding `.beads/`. Comparing them guarantees a permanent DIVERGENT verdict —
  # one backend keeps `.beads/issues.jsonl`, the other `docs/tasks/*.md`, BY DESIGN — and a
  # differential that can never say IDENTICAL teaches its reader to ignore it. What the two
  # backends owe is the same PRODUCT, not the same bookkeeping.
  local owned ids
  owned=$(tk "$repo" backend --json \
          | python3 -c 'import json,sys; print("\n".join(json.load(sys.stdin)["owned_paths"]))')
  # Ids appear inside paths too — `docs/proposed/<epic-id>-normalise/tasks.md` — and the
  # two backends generate different id schemes by construction.
  ids=$(tk "$repo" list --json \
        | python3 -c 'import json,sys; print("\n".join(t["id"] for t in json.load(sys.stdin)))')
  ( cd "$repo" && for b in $(git branch --format='%(refname:short)' | grep harness-w); do
        git diff --name-only master "$b"
    done | sort -u ) | OWNED="$owned" IDS="$ids" python3 -c '
import os, re, sys
owned = [p for p in os.environ["OWNED"].splitlines() if p]
ids = sorted((i for i in os.environ["IDS"].splitlines() if i), key=len, reverse=True)
for line in sys.stdin.read().splitlines():
    if any(line.startswith(p) for p in owned):
        continue
    for i in ids:
        line = line.replace(i, "<id>")
    print("file   " + line)
' | sort -u
  # COMMIT COUNT, NOT SUBJECT. The subject is agent-authored prose — one run wrote
  # "add normalise_email helper" and the other "add normalise_email" — and reporting that
  # as a divergence would train a reader to ignore this output. What the backend owes is
  # that each task produced exactly one commit.
  #
  # COUNTED BY TASK REFERENCE, NOT BY BRANCH RANGE. `master..<branch>` is empty for every
  # branch that has been merged, so counting that way reported a confident `0` for each and
  # would have gone on reporting it however many commits a worker made. The contract the
  # worker is actually held to is "exactly one commit referencing the task id", so count
  # that — it survives merging, and it is the property worth asserting.
  # SUBJECTS ONLY, AND TASKS ONLY. `--grep` searches the whole commit message, so a body
  # reading "depends on <sibling>" was counted as that sibling's own commit.
  local task_ids
  task_ids=$(tk "$repo" list --json \
        | python3 -c 'import json,sys; print("\n".join(t["id"] for t in json.load(sys.stdin) if t["type"] != "epic" and not t["title"].startswith("Permission:")))')
  ( cd "$repo" && git log --all --no-merges --format=%s ) \
    | TASK_IDS="$task_ids" python3 -c '
import os, re, sys
from collections import Counter
subjects = sys.stdin.read().splitlines()
ids = [i for i in os.environ["TASK_IDS"].splitlines() if i]
counts = Counter()
for i in ids:
    # THE EPIC ID IS A PREFIX OF EVERY TASK ID under one backend (e-1 vs e-1.2), so a
    # bare substring test credits the parent with all of its children. Require that the
    # id is not followed by another id character.
    pat = re.compile(re.escape(i) + r"(?![\w.])")
    counts[sum(1 for s in subjects if pat.search(s))] += 1
for n, tasks in sorted(counts.items()):
    print("tasks-with {}-commit x{}".format(n, tasks))
'
}

A=$(mktemp); B=$(mktemp)
facts beads   > "$A"
facts mdfiles > "$B"

echo "== differential: beads vs mdfiles =="
if diff -u "$A" "$B" > /dev/null; then
  echo "IDENTICAL — the same base and the same work produced the same outcome on both."
  echo
  sed 's/^/  /' "$A"
  rm -f "$A" "$B"; exit 0
fi
echo "DIVERGENT. Every line here is a difference the conformance contract does not cover:"
echo
diff -u --label beads "$A" --label mdfiles "$B" | tail -n +3 | sed 's/^/  /'
rm -f "$A" "$B"
exit 1
