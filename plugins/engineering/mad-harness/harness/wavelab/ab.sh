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
ENVS=()
arm_envs() {  # $1 = off|on  -> fills ENVS
  ENVS=(MAD_HARNESS_CACHE_TTL= MAD_HARNESS_STATIC_PREFIX= MAD_HARNESS_STAGGER_SECONDS=
        MAD_HARNESS_TASK_BUDGET_TOKENS= MAD_HARNESS_PRELOAD=)
  case "$LEVER:$1" in
    cache_ttl:on)      ENVS+=(MAD_HARNESS_CACHE_TTL=5m) ;;
    static_prefix:on)  ENVS+=(MAD_HARNESS_STATIC_PREFIX=1) ;;
    stagger:off)       ENVS+=(MAD_HARNESS_STATIC_PREFIX=1) ;;
    stagger:on)        ENVS+=(MAD_HARNESS_STATIC_PREFIX=1 MAD_HARNESS_STAGGER_SECONDS=8) ;;
    task_budget:on)    ENVS+=(MAD_HARNESS_TASK_BUDGET_TOKENS=400000) ;;
    preload:on)        ENVS+=(MAD_HARNESS_PRELOAD=evidence-gathering) ;;
  esac
}

mkdir -p "$ROOT"

# FROZEN CODE. The first series ran from the live working tree; edits to harness/tracker/
# made mid-series tripped the fresh check and aborted every remaining lever in four
# seconds — and had it not aborted, later runs would have run different code from earlier
# ones, which is a confound no report could detect. An experiment pins its code: the
# plugin at HEAD is checked out, detached, into the series root, every run dispatches
# from that copy, and the commit is recorded beside each run. Continue developing freely.
AIM="$(cd "$HERE/../../../../.." && git rev-parse --show-toplevel)"
SHA="$(git -C "$AIM" rev-parse --short=12 HEAD)"
FROZEN="$ROOT/plugin-$SHA"
if [ ! -d "$FROZEN" ]; then
  echo "== freezing the plugin at $SHA into $FROZEN"
  git -C "$AIM" worktree add --detach -q "$FROZEN" "$SHA"
fi
FLAB="$FROZEN/plugins/engineering/mad-harness/harness/wavelab"
[ -x "$FLAB/reset.sh" ] || { echo "frozen tree has no wavelab at $FLAB" >&2; exit 3; }
if [ -n "$(git -C "$AIM" status --porcelain -- plugins/engineering/mad-harness/harness plugins/engineering/mad-harness/agents plugins/engineering/mad-harness/skills plugins/engineering/mad-harness/commands)" ]; then
  echo "   note: the live tree has uncommitted changes; the series runs $SHA, not them"
fi
export WAVELAB_SKIP_FRESH=1
echo "== A/B $LEVER: arms [$ARMS] x $RUNS run(s), fan-out $FANOUT, backend $BACKEND, root $ROOT, code $SHA"
IFS=',' read -r -a ARM_LIST <<< "$ARMS"
for ARM in "${ARM_LIST[@]}"; do
  [ "$ARM" = "baseline" ] && ARM=off
  for RUN in $(seq 1 "$RUNS"); do
    RUN_ROOT="$ROOT/$LEVER-$ARM-$RUN"
    if [ -f "$RUN_ROOT/.ab-done" ]; then echo "-- $LEVER:$ARM:$RUN already done, skipping"; continue; fi
    echo; echo "== $LEVER:$ARM:$RUN  ($RUN_ROOT)"
    rm -rf "$RUN_ROOT"
    "$FLAB/reset.sh" --root "$RUN_ROOT" --only "$BACKEND" --fanout "$FANOUT" --cap "$FANOUT" || {
      echo "!! reset failed for $LEVER:$ARM:$RUN — stopping this lever" >&2; exit 4; }
    # The label every dispatch event in this run carries.
    arm_envs "$ARM"
    ENVS+=("MAD_HARNESS_EXPERIMENT=$LEVER:$ARM:$RUN")
    env "${ENVS[@]}" "$FLAB/dispatch-wave.sh" --root "$RUN_ROOT" "$BACKEND" || true
    env "${ENVS[@]}" "$FLAB/merge-wave.sh" --root "$RUN_ROOT" "$BACKEND" || true
    if [ "$WAVE1_ONLY" = "0" ]; then
      env "${ENVS[@]}" "$FLAB/dispatch-wave.sh" --root "$RUN_ROOT" "$BACKEND" || true
      env "${ENVS[@]}" "$FLAB/merge-wave.sh" --root "$RUN_ROOT" "$BACKEND" || true
    fi
    echo "$SHA" > "$RUN_ROOT/.ab-sha"
    date -u +"%Y-%m-%dT%H:%M:%SZ" > "$RUN_ROOT/.ab-done"
  done
done
echo; echo "== done. Read it back:  $HERE/ab-report.sh $LEVER --root $ROOT"
