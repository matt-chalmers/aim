#!/usr/bin/env bash
# Dispatch one real wave of workers against a wavelab repo.
#
#   harness/wavelab/dispatch-wave.sh [--root DIR] [--lens] <beads|mdfiles>
#
# REAL AGENTS, at their declared tiers. This is the part no other test reaches: whether a
# dispatched worker can reach `tk.sh` through the boundary, whether `.swarm-env` carries a
# usable identity, and whether two workers racing for the same task actually exclude each
# other. Everything up to the dispatch is already covered by test_wave_integration.py.
#
# THROUGH THE PRODUCTION SCRIPTS since 0.10.22: `swarm/wave-plan.sh` composes the wave
# (the ready set for the lane at its cap, resume points, shared paths dropped) and opens
# the wave manifest; `swarm/fanout.sh` runs the dispatches at once, each with a timeout,
# and records `dispatched` on the manifest; `--lens` is `lens-wave.sh`, which is
# `swarm/lens-gate.sh` per task. The shared-vocabulary and new-file checks, the routing of
# a FAIL and the breakers' actions are the orchestrator's judgement, and an agent reading
# /swarm is what exercises those. Until 0.10.22 this was its own dispatch loop with a
# hand-written prompt template — a lab-grade copy of what /swarm had the orchestrator do.
set -euo pipefail


ROOT="${WAVELAB_ROOT:-$HOME/harness-wavelab}"
LENS=0
while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOT="${2:?}"; shift ;;
    --lens) LENS=1 ;;
    --stagger) STAGGER="${2:?--stagger needs seconds}"; shift ;;
    *) NAME="$1" ;;
  esac
  shift
done
NAME="${NAME:?usage: dispatch-wave.sh [--root DIR] [--lens] [--stagger SECONDS] <beads|mdfiles>}"
REPO="$ROOT/$NAME"
[ -d "$REPO" ] || { echo "no such repo: $REPO — run reset.sh first" >&2; exit 2; }

HERE="$(cd "$(dirname "$0")" && pwd)"
HARNESS="$(cd "$HERE/.." && pwd)"
# THE LAB POINTS AT ITS TARGET BY STANDING IN IT. It used to export MAD_HARNESS_REPO,
# which every wrapper — and every agent dispatch.py spawned, since it inherits the
# environment — then honoured ahead of resolving anything. So the one question a real
# project needs answered ("which repository?") was answered for it, and a resolver that
# returned the plugin's own directory passed every wave here while a real project got
# an empty backlog with exit 0. Nothing in this lab sets MAD_HARNESS_REPO or
# MAD_HARNESS_CALLER_PWD: the wrappers record the caller's directory themselves.
cd "$REPO" || exit 2
SCRATCH="${SCRATCHPAD:-${TMPDIR:-/tmp}}/wavelab-$NAME"
mkdir -p "$SCRATCH"

tk() { "$HARNESS/tracker/tk.sh" "$@"; }

echo "== wavelab wave: $NAME =="
tk backend --json

# /swarm pre-flight. Without it the backend stages its own export into whatever commit
# comes next, the primary checkout ends the wave dirty, and the merge is refused.
tk autosync off

# THE SEEDED EPIC'S TASKS ONLY, as /swarm scopes a wave to one epic. A worker filed a bug
# it found into the tracker mid-task — correctly — and the unscoped ready set dispatched
# that bug as the next wave's work, where a worker spent the whole ceiling trying to fix
# the harness from inside the lab repository. `wave-plan.sh --parent` is that scope, plus
# the lane cap, the resume point of every candidate and the contention cut — and it opens
# the epic's wave manifest, which fanout and the lens gate then write to.
EPIC=$(tk list --type epic --json | python3 -c 'import json,sys; r=[t for t in json.load(sys.stdin) if t["status"]!="closed"]; print(r[0]["id"] if r else "")')
[ -n "$EPIC" ] || { echo "no open epic — run seed-epic.sh"; exit 0; }
set +e
PLAN=$("$HARNESS/swarm/wave-plan.sh" backend --parent "$EPIC" --json 2> "$SCRATCH/wave-plan.err")
PRC=$?
set -e
if [ "$PRC" -ne 0 ]; then
  cat "$SCRATCH/wave-plan.err"
  echo "${PLAN:-nothing ready — the epic may be complete}"
  exit 0
fi
READY=$(printf '%s' "$PLAN" | python3 -c 'import json,sys; p=json.load(sys.stdin); print(" ".join(r["id"] for r in p["wave"]))')
MANIFEST=$(printf '%s' "$PLAN" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("manifest",""))')
[ -n "$READY" ] || { echo "nothing ready — the epic may be complete"; exit 0; }
echo "ready: $READY"
[ -n "$MANIFEST" ] && echo "manifest: $MANIFEST"

# One dispatch per task, all at once — the shape /swarm requires, and the only shape that
# tests the claim mutex under real concurrency. The prompt is the worker's task record
# plus the lab's four-line contract; `dispatch.sh` injects the card, the whereabouts and
# the conventions, and the worker's doctrine rides in its system prompt.
# STAGGER: worker 1 alone first, so its cache write is warm before the rest read it. A
# fan-out of N cold prefixes costs N x 1.25P; sequenced it is 1.25P + 0.1(N-1)P — at N=8
# that is 5x. Only true when the prefix is static (the static_prefix lever); measured.
STAGGER="${STAGGER:-${MAD_HARNESS_STAGGER_SECONDS:-0}}"
JOBS="$SCRATCH/jobs.txt"; : > "$JOBS"
N=0
for TASK in $READY; do
  N=$((N+1))
  PROMPT="$SCRATCH/prompt-$TASK.txt"
  tk show "$TASK" --json | python3 -c "
import json, sys
t = json.load(sys.stdin)[0]
print(f'''You are implementing one task in an isolated git worktree.

TASK {t['id']} — {t['title']}

{t['description']}

HOW THIS REPOSITORY WORKS
- Python, managed by uv. Run tests with: uv run pytest
- Source lives under src/wavelab/, tests under tests/.
- The suite is green right now. Leave it green.

THE TRACKER
Every tracker command goes through the shim, never through a backend directly:
  \$HARNESS_ROOT/tracker/tk.sh claim {t['id']}
  \$HARNESS_ROOT/tracker/tk.sh note {t['id']} \"<what you did>\"
  \$HARNESS_ROOT/tracker/tk.sh close {t['id']} --reason \"<what shipped, how verified>\"

YOUR CONTRACT
1. Claim the task FIRST. If it reports already claimed by someone else, stop and return
   SKIPPED — never steal it, never pick a different one.
2. Implement it to the acceptance criteria. Write the tests it names.
3. Run the full suite and watch it pass.
4. Commit exactly one clean commit referencing the task id. Do not push.
5. Close the task with a reason, then return at most ten lines:
   <id> · PASS|FAIL|BLOCKED|SKIPPED · files touched · tests run and result · commit sha
''')" > "$PROMPT"
  printf '%q %q --prompt-file %q --worker %q --task %q --lane backend\n' \
    "$HARNESS/models/dispatch.sh" fullstack-engineer "$PROMPT" "$N" "$TASK" >> "$JOBS"
done

FAN="$SCRATCH/fanout"; rm -rf "$FAN" "$FAN-rest"
WAVE_ARG=(); [ -n "$MANIFEST" ] && WAVE_ARG=(--wave "$MANIFEST")
echo "-- dispatching $N worker(s) through fanout"
set +e
if [ "$STAGGER" -gt 0 ] && [ "$N" -gt 1 ]; then
  head -1 "$JOBS" > "$SCRATCH/jobs-1.txt"; tail -n +2 "$JOBS" > "$SCRATCH/jobs-rest.txt"
  "$HARNESS/swarm/fanout.sh" --jobs "$SCRATCH/jobs-1.txt" --cap 1 --out-dir "$FAN" "${WAVE_ARG[@]}"
  echo "-- stagger: waited for worker 1; the rest follow"
  "$HARNESS/swarm/fanout.sh" --jobs "$SCRATCH/jobs-rest.txt" --cap "$N" --out-dir "$FAN-rest" "${WAVE_ARG[@]}"
  # Re-index the second batch after the first so job-i lines up with READY's order.
  i=1; for f in "$FAN-rest"/job-*.json; do [ -e "$f" ] || continue; b=$(basename "${f%.json}"); for ext in out err json rc; do mv "$FAN-rest/$b.$ext" "$FAN/job-$i.$ext"; done; i=$((i+1)); done
else
  "$HARNESS/swarm/fanout.sh" --jobs "$JOBS" --cap "$N" --out-dir "$FAN" "${WAVE_ARG[@]}"
fi
set -e
# The lab's files, by task, from fanout's by-index files (job-i is READY's i-th task).
i=0
for TASK in $READY; do
  cp "$FAN/job-$i.out" "$SCRATCH/out-$TASK.txt" 2>/dev/null || : > "$SCRATCH/out-$TASK.txt"
  cp "$FAN/job-$i.err" "$SCRATCH/err-$TASK.txt" 2>/dev/null || : > "$SCRATCH/err-$TASK.txt"
  cp "$FAN/job-$i.rc" "$SCRATCH/rc-$TASK" 2>/dev/null || echo "?" > "$SCRATCH/rc-$TASK"
  i=$((i+1))
done

echo
FAILED=0; WARNED=0
for TASK in $READY; do
  RC=$(cat "$SCRATCH/rc-$TASK" 2>/dev/null || echo "?")
  echo "== $TASK  (exit $RC)"
  # DENIALS ARE ON STDERR, and reading only stdout is how this wave reported "zero
  # denials" while telemetry recorded one per worker.
  if grep -q 'permission denial' "$SCRATCH/err-$TASK.txt" 2>/dev/null; then
    echo "   !! $(grep -o '[0-9]* permission denial(s)' "$SCRATCH/err-$TASK.txt" | head -1) — see err-$TASK.txt"
    grep -A6 'permission denial' "$SCRATCH/err-$TASK.txt" | tail -n +2 | cut -c1-120 | sed 's/^/      /'
  fi
  # A DENIAL IS NOT AUTOMATICALLY A FAILED TASK. dispatch.sh marks any denial not-ok,
  # which is right as a signal — a worker denied its test command can still report PASS.
  # But a worker that was refused one exploratory command and then finished the work
  # correctly is a different thing from one that could not do the work at all, and
  # collapsing the two teaches a reader to ignore the exit code. So the wave asks the
  # tracker: denied AND the task is still open is a failure; denied AND closed is a
  # warning worth reading.
  if [ "$RC" != "0" ]; then
    if tk show "$TASK" --json 2>/dev/null | grep -q '"status": *"closed"'; then
      echo "   (dispatch not-ok, but $TASK closed — denial did not prevent the work)"
      WARNED=$((WARNED+1))
    else
      FAILED=$((FAILED+1))
    fi
  fi
  tail -12 "$SCRATCH/out-$TASK.txt" 2>/dev/null || echo "  (no output)"
  echo
done

# The epic's view is regenerated by merge-wave.sh's close-wave --sync-only, as /swarm
# step 9 does; nothing here renders it early.

echo "== tracker state after the wave =="
tk list | sed "s/^/  /"

# One implementation of the lens pass, in lens-wave.sh, so a task can be re-judged
# without re-running the wave.
if [ "$LENS" = "1" ]; then
  echo
  "$HERE/lens-wave.sh" --root "$ROOT" "$NAME" $READY
fi

# Restore what pre-flight disabled. Nothing else does, and left off the backend stops
# keeping its tracked export fresh.
tk autosync on

echo
echo "artefacts: $SCRATCH"
[ "$WARNED" -gt 0 ] && echo "!! $WARNED dispatch(es) hit a denial but completed the task anyway"
if [ "$FAILED" -gt 0 ]; then
  echo "!! $FAILED of $N dispatch(es) returned NOT OK AND left the task open"
  exit 1
fi
