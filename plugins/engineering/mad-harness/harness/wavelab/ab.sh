#!/usr/bin/env bash
# A/B one cost lever: the same seeded epic, N runs with the lever off and N with it on.
#
#   harness/wavelab/ab.sh <lever> [--runs N] [--fanout N] [--root DIR] [--backend beads|mdfiles]
#                          [--wave1-only] [--arms off,on|baseline|on]
#
#   levers:  cache_ttl        off = the CLI's default (1h on a subscription)   on = 5m
#            static_prefix    off = today's system prompt                      on = static prefix
#            stagger          off = static prefix, no stagger                  on = static prefix + 8s
#            task_budget      off = none                                       on = 400000 tokens
#            preload          off = today's writers                            on = + evidence-gathering
#
# WHY A RIG AND NOT A FIELD RUN. A field campaign runs different tasks every time and the
# cost analysis that motivated this measured 30x token variance on IDENTICAL tasks; it can
# show a lever's direction, never its size. This runs the SAME epic in a fresh repository
# per run, tags every dispatch event with lever:arm:run, and leaves the series for
# ab-report.sh. One lever at a time against the same baseline, never stacked.
#
# Each run is a fresh --root, because reset.sh wipes .harness/run/events/ and the whole
# point is the events. Cost: ~$2.50-3 per two-wave run at fan-out 2 (measured, 0.9.x);
# --wave1-only halves that and is enough for the cache levers, whose effect is on the
# parallel wave.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LEVER="${1:?usage: ab.sh <lever> [--runs N] [--fanout N] [--root DIR] [--backend B] [--wave1-only] [--arms LIST]}"
shift
RUNS=5; FANOUT=2; ROOT="${WAVELAB_AB_ROOT:-$HOME/harness-wavelab-ab}"; BACKEND=beads; WAVE1_ONLY=0; ARMS="off,on"
while [ $# -gt 0 ]; do
  case "$1" in
    --runs) RUNS="${2:?}"; shift ;;
    --fanout) FANOUT="${2:?}"; shift ;;
    --root) ROOT="${2:?}"; shift ;;
    --backend) BACKEND="${2:?}"; shift ;;
    --wave1-only) WAVE1_ONLY=1 ;;
    --arms) ARMS="${2:?}"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
case "$LEVER" in cache_ttl|static_prefix|stagger|task_budget|preload) ;; *) echo "unknown lever: $LEVER" >&2; exit 2 ;; esac

# The environment each arm dispatches under. Everything else is inherited unchanged, and
# every arm clears the levers it does not set, so a stray export cannot leak into an arm.
arm_env() {  # $1 = off|on  -> prints VAR=value lines
  echo "MAD_HARNESS_CACHE_TTL="; echo "MAD_HARNESS_STATIC_PREFIX="; echo "MAD_HARNESS_STAGGER_SECONDS="
  echo "MAD_HARNESS_TASK_BUDGET_TOKENS="; echo "MAD_HARNESS_PRELOAD="
  case "$LEVER:$1" in
    cache_ttl:on)      echo "MAD_HARNESS_CACHE_TTL=5m" ;;
    static_prefix:on)  echo "MAD_HARNESS_STATIC_PREFIX=1" ;;
    stagger:off)       echo "MAD_HARNESS_STATIC_PREFIX=1" ;;
    stagger:on)        echo "MAD_HARNESS_STATIC_PREFIX=1"; echo "MAD_HARNESS_STAGGER_SECONDS=8" ;;
    task_budget:on)    echo "MAD_HARNESS_TASK_BUDGET_TOKENS=400000" ;;
    preload:on)        echo "MAD_HARNESS_PRELOAD=evidence-gathering" ;;
  esac
}

mkdir -p "$ROOT"
echo "== A/B $LEVER: arms [$ARMS] x $RUNS run(s), fan-out $FANOUT, backend $BACKEND, root $ROOT"
IFS=',' read -r -a ARM_LIST <<< "$ARMS"
for ARM in "${ARM_LIST[@]}"; do
  [ "$ARM" = "baseline" ] && ARM=off
  for RUN in $(seq 1 "$RUNS"); do
    RUN_ROOT="$ROOT/$LEVER-$ARM-$RUN"
    if [ -f "$RUN_ROOT/.ab-done" ]; then echo "-- $LEVER:$ARM:$RUN already done, skipping"; continue; fi
    echo; echo "== $LEVER:$ARM:$RUN  ($RUN_ROOT)"
    rm -rf "$RUN_ROOT"
    "$HERE/reset.sh" --root "$RUN_ROOT" --only "$BACKEND" --fanout "$FANOUT" --cap "$FANOUT"
    # The label every dispatch event in this run carries.
    ENVS=("MAD_HARNESS_EXPERIMENT=$LEVER:$ARM:$RUN")
    while IFS= read -r kv; do ENVS+=("$kv"); done < <(arm_env "$ARM")
    env "${ENVS[@]}" "$HERE/dispatch-wave.sh" --root "$RUN_ROOT" "$BACKEND" || true
    env "${ENVS[@]}" "$HERE/merge-wave.sh" --root "$RUN_ROOT" "$BACKEND" || true
    if [ "$WAVE1_ONLY" = "0" ]; then
      env "${ENVS[@]}" "$HERE/dispatch-wave.sh" --root "$RUN_ROOT" "$BACKEND" || true
      env "${ENVS[@]}" "$HERE/merge-wave.sh" --root "$RUN_ROOT" "$BACKEND" || true
    fi
    date -u +"%Y-%m-%dT%H:%M:%SZ" > "$RUN_ROOT/.ab-done"
  done
done
echo; echo "== done. Read it back:  $HERE/ab-report.sh $LEVER --root $ROOT"
