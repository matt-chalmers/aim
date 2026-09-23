#!/usr/bin/env bash
# Run the verification lenses over tasks that have already landed — through the
# PRODUCTION gate, `harness/swarm/lens-gate.sh`, one call per task.
#
#   harness/wavelab/lens-wave.sh [--root DIR] <beads|mdfiles> [task-id ...]
#
# Separate from dispatch-wave.sh because judging is not dispatching: a task can be
# re-judged without rebuilding the repo or spending another wave, which is what makes the
# lens path testable at all. dispatch-wave.sh --lens calls straight into this.
#
# UNTIL 0.10.21 THIS WAS ITS OWN GATE: three prompts written here, three dispatches, a
# grep for `VERDICT:` — a lab-grade copy of what /swarm step 7 had the orchestrator do by
# hand, with the same two defects (L3 handed a brief that named the diff's paths; the
# lenses run in the primary at main, before the branch was merged). It now calls the one
# gate production uses, so the lab exercises the production path and the two cannot drift.
# `.harness/run/lens-verdicts.txt` keeps the `task<TAB>agent<TAB>PASS|FAIL|NONE` rows
# `ab-report.sh` reads, from the gate's --json facts.
set -euo pipefail

ROOT="${WAVELAB_ROOT:-$HOME/harness-wavelab}"
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOT="${2:?}"; shift ;;
    *) ARGS+=("$1") ;;
  esac
  shift
done
NAME="${ARGS[0]:?usage: lens-wave.sh [--root DIR] <beads|mdfiles> [task-id ...]}"
TASKS=("${ARGS[@]:1}")

REPO="$ROOT/$NAME"
[ -d "$REPO" ] || { echo "no such repo: $REPO — run reset.sh first" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
HARNESS="$(cd "$HERE/.." && pwd)"
# THE LAB POINTS AT ITS TARGET BY STANDING IN IT — nothing here sets MAD_HARNESS_REPO or
# MAD_HARNESS_CALLER_PWD; the wrappers record the caller's directory themselves.
cd "$REPO" || exit 2
SCRATCH="${SCRATCHPAD:-${TMPDIR:-/tmp}}/wavelab-$NAME"
mkdir -p "$SCRATCH"

tk() { "$HARNESS/tracker/tk.sh" "$@"; }

# No ids given: the tasks THIS WAVE LANDED, from its manifest — `merged` where the wave got
# that far, else `dispatched`. It used to be "every closed task", which silently judged
# NOTHING from 0.10.28 on: that release stopped a worker closing its own task (the
# orchestrator closes it after the lenses pass), so by the time this ran there were no
# closed tasks and every `--lenses` series measured cost with no quality counterweight —
# the one thing the flag exists to provide. Closed tasks remain the fallback for a repo
# with no manifest.
if [ "${#TASKS[@]}" -eq 0 ]; then
  MANIFEST=$(ls -t "$REPO"/.harness/run/waves/*.json 2>/dev/null | grep -v '\.lock$' | head -1 || true)
  if [ -n "$MANIFEST" ]; then
    mapfile -t TASKS < <(python3 -c '
import json, sys
m = json.load(open(sys.argv[1]))
ids = [x["task"] for x in (m.get("merged") or []) if x.get("task")]
print("\n".join(ids or sorted(m.get("dispatched") or {})))' "$MANIFEST")
    [ "${#TASKS[@]}" -gt 0 ] && echo "judging this wave: $(basename "$MANIFEST") — ${TASKS[*]}"
  fi
fi
if [ "${#TASKS[@]}" -eq 0 ]; then
  mapfile -t TASKS < <(tk list --json | python3 -c '
import json, sys
for t in json.load(sys.stdin):
    if t["type"] != "epic" and t["status"] == "closed":
        print(t["id"])')
fi
[ "${#TASKS[@]}" -gt 0 ] || { echo "nothing landed or closed to judge"; exit 0; }

echo "== verification lenses: $NAME =="
VERDICTS="$SCRATCH/verdicts.txt"; : > "$VERDICTS"
KEPT="$REPO/.harness/run/lens-verdicts.txt"; mkdir -p "$(dirname "$KEPT")"

FAILED=0
for TASK in "${TASKS[@]}"; do
  SHA=$(git log --all --oneline --grep="$TASK" -1 --format=%H || true)
  [ -n "$SHA" ] || { echo "  $TASK: no commit found, skipping"; continue; }
  # The branch holding the commit, if a worker branch still does — the gate runs the
  # suite and L2/L3 there. A commit already merged into main needs no --branch.
  BRANCH=$(git for-each-ref --format='%(refname:short)' 'refs/heads/harness-w*' --contains "$SHA" | head -1 || true)
  ARGS=("$TASK" "$SHA" --lane backend --json)
  if [ -n "$BRANCH" ] && ! git merge-base --is-ancestor "$SHA" main 2>/dev/null; then
    ARGS+=(--branch "$BRANCH")
  fi
  echo "-- $TASK  $SHA"
  set +e
  "$HARNESS/swarm/lens-gate.sh" "${ARGS[@]}" > "$SCRATCH/gate-$TASK.txt" 2> "$SCRATCH/gate-$TASK.err"
  RC=$?
  set -e
  sed 's/^/   /' "$SCRATCH/gate-$TASK.txt" | grep -E '^\s+\[|VERDICT|COULD NOT' || true
  # The last line is the --json facts: {"lenses": {"L1": "PASS", ...}, ...}
  tail -1 "$SCRATCH/gate-$TASK.txt" | python3 -c '
import json, sys
task = sys.argv[1]
agents = {"L1": "verifier", "L2": "verifier-tests", "L3": "verifier-spec", "L4": "verifier-security"}
try:
    facts = json.loads(sys.stdin.read())
except Exception:
    facts = {}
for lens, agent in agents.items():
    v = (facts.get("lenses") or {}).get(lens)
    if v is None and lens == "L4":
        continue
    print(f"{task}\t{agent}\t{v or 'NONE'}")
' "$TASK" | tee -a "$VERDICTS" >> "$KEPT"
  [ "$RC" -eq 0 ] || FAILED=$((FAILED+1))
done

echo
echo "== verdicts =="
cat "$VERDICTS"
echo "full lens output: $SCRATCH"
[ "$FAILED" -eq 0 ]
