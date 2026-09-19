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
# NOT a reimplementation of /swarm. It runs the ready set at the lane cap and, with
# --lens, the correctness lens over each result. The contention matrix, the unanimity
# rule and the circuit breakers are the orchestrator's judgement, and an agent reading
# /swarm is what exercises those.
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
# the harness from inside the lab repository.
EPIC=$(tk list --type epic --json | python3 -c 'import json,sys; r=[t for t in json.load(sys.stdin) if t["status"]!="closed"]; print(r[0]["id"] if r else "")')
READY=$(tk ready --parent "$EPIC" --json | python3 -c '
import json, sys
rows = json.load(sys.stdin)
print(" ".join(t["id"] for t in rows if t["type"] != "epic"))')
[ -n "$READY" ] || { echo "nothing ready — the epic may be complete"; exit 0; }
echo "ready: $READY"

# One background dispatch per task, all launched together — the shape /swarm requires, and
# the only shape that tests the claim mutex under real concurrency.
# STAGGER: worker 1 alone first, so its cache write is warm before the rest read it. A
# fan-out of N cold prefixes costs N x 1.25P; sequenced it is 1.25P + 0.1(N-1)P — at N=8
# that is 5x. Only true when the prefix is static (the static_prefix lever); measured.
STAGGER="${STAGGER:-${MAD_HARNESS_STAGGER_SECONDS:-0}}"
PIDS=(); N=0
for TASK in $READY; do
  if [ "$N" = "1" ] && [ "$STAGGER" -gt 0 ]; then
    echo "-- stagger: waiting ${STAGGER}s for worker 1's first request before the rest"
    sleep "$STAGGER"
  fi
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

  echo "-- dispatching worker $N on $TASK"
  # `set +e` FIRST, or the exit code is never recorded. Under `set -e` a non-zero
  # dispatch kills this subshell before the `echo` runs, so no rc file is written at
  # all — and every wave printed "(exit ?)" while two workers were in fact returning
  # NOT OK. The wave then judged success from the text a worker returned rather than
  # from its status, which is the believe-the-report failure this lab exists to catch.
  ( set +e
    "$HARNESS/models/dispatch.sh" fullstack-engineer \
      --prompt-file "$PROMPT" --worker "$N" --task "$TASK" --lane backend \
      > "$SCRATCH/out-$TASK.txt" 2> "$SCRATCH/err-$TASK.txt"
    echo "$?" > "$SCRATCH/rc-$TASK" ) &
  PIDS+=($!)
done

echo "-- ${#PIDS[@]} workers in flight, waiting..."
for p in "${PIDS[@]}"; do wait "$p" || true; done

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

# Regenerate the epic's view, as /swarm step 9 and campaign-loop §4 now do. Without it
# the staging folder still shows the plan as it stood at seed time — which is precisely
# the rot the `--check` mode exists to catch.
EPIC=$(tk list --type epic --json | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r[0]["id"] if r else "")')
if [ -n "$EPIC" ]; then
  VIEW=$(ls -d "$REPO"/docs/proposed/"$EPIC"* 2>/dev/null | head -1)
  [ -n "$VIEW" ] && "$HARNESS/tracker/render-epic.sh" "$EPIC" --write "$VIEW/tasks.md"
fi

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
