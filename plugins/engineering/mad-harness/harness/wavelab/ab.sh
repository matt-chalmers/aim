#!/usr/bin/env bash
# A/B one cost lever: the same seeded epic, N runs with the lever off and N with it on.
#
#   harness/wavelab/ab.sh <lever> [--runs N] [--fanout N] [--root DIR] [--backend beads|mdfiles]
#                          [--wave1-only] [--lenses] [--arms off,on|baseline|on]
#                          [--pin-off SHA] [--orchestrated]
#
#   levers:  cache_ttl        off = the CLI's default (1h on a subscription)   on = 5m
#            static_prefix    off = today's system prompt                      on = static prefix
#            stagger          off = static prefix, no stagger                  on = static prefix + 8s
#            task_budget      off = none                                       on = 400000 tokens
#            preload          off = today's writers                            on = + evidence-gathering
#            lean_catalog     off = the CLI's full Skill catalog                on = plugin + project skills only
#            release          off = the plugin at --pin-off (default 0.10.18)  on = the plugin at HEAD
#
# TWO CODES, NOT TWO ENVIRONMENTS. Every lever above is one environment variable read by
# the same code; `release` is the one lever where the ARMS ARE DIFFERENT COMMITS — the off
# arm dispatches from a tree frozen at --pin-off, the on arm from HEAD, each running its
# own wavelab scripts. It exists to measure the 0.10.19–0.10.27 series ("the deterministic
# steps out of the prose") against the last release before it.
#
# --orchestrated: instead of this script's own wave loop (dispatch-wave, merge-wave, the
# lenses), ONE headless campaign-orchestrator runs the whole epic through the frozen tree's
# `campaign.sh --epic`, exactly as /campaign-auto does. Its dispatch event carries `turns`
# and `cost_usd`: the orchestrator's own price for an epic, which the shell loop cannot
# measure because it has no orchestrator. The lab repo is given a bare remote first so the
# close-out's push is real. The orchestrator's ceiling bounds the arm (see check-model-config).
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
LEVER="${1:?usage: ab.sh <lever> [--runs N] [--fanout N] [--root DIR] [--backend B] [--wave1-only] [--lenses] [--arms LIST]}"
shift
RUNS=5; FANOUT=2; ROOT="${WAVELAB_AB_ROOT:-$HOME/harness-wavelab-ab}"; BACKEND=beads; WAVE1_ONLY=0; ARMS="off,on"; LENSES=0
# 0.10.18: the last release before the prose-to-code series (0.10.19 is 332a274).
PIN_OFF="e2fa8d5d1226"; ORCHESTRATED=0
while [ $# -gt 0 ]; do
  case "$1" in
    --runs) RUNS="${2:?}"; shift ;;
    --fanout) FANOUT="${2:?}"; shift ;;
    --root) ROOT="${2:?}"; shift ;;
    --backend) BACKEND="${2:?}"; shift ;;
    --wave1-only) WAVE1_ONLY=1 ;;
    --lenses) LENSES=1 ;;
    --arms) ARMS="${2:?}"; shift ;;
    --pin-off) PIN_OFF="${2:?}"; shift ;;
    --orchestrated) ORCHESTRATED=1 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
case "$LEVER" in cache_ttl|static_prefix|stagger|task_budget|preload|lean_catalog|release) ;; *) echo "unknown lever: $LEVER" >&2; exit 2 ;; esac

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
    lean_catalog:off)  ENVS+=(MAD_HARNESS_LEAN_CATALOG=0) ;;
    lean_catalog:on)   ENVS+=(MAD_HARNESS_LEAN_CATALOG=1) ;;
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
freeze() {  # $1 = commit-ish -> prints the frozen plugin root, creating it once
  local sha; sha="$(git -C "$AIM" rev-parse --short=12 "$1")"
  local dir="$ROOT/plugin-$sha"
  if [ ! -d "$dir" ]; then
    echo "== freezing the plugin at $sha into $dir" >&2
    git -C "$AIM" worktree add --detach -q "$dir" "$sha"
  fi
  echo "$dir"
}
SHA="$(git -C "$AIM" rev-parse --short=12 HEAD)"
FROZEN="$(freeze HEAD)"
FROZEN_OFF="$FROZEN"; SHA_OFF="$SHA"
if [ "$LEVER" = "release" ]; then
  FROZEN_OFF="$(freeze "$PIN_OFF")"; SHA_OFF="$(git -C "$AIM" rev-parse --short=12 "$PIN_OFF")"
  echo "== release: off = $SHA_OFF ($(git -C "$AIM" show "$SHA_OFF:plugins/engineering/mad-harness/.claude-plugin/plugin.json" | python3 -c 'import json,sys;print(json.load(sys.stdin)["version"])'))   on = $SHA (HEAD)"
fi
for d in "$FROZEN" "$FROZEN_OFF"; do
  [ -x "$d/plugins/engineering/mad-harness/harness/wavelab/reset.sh" ] || { echo "frozen tree has no wavelab under $d" >&2; exit 3; }
done
if [ -n "$(git -C "$AIM" status --porcelain -- plugins/engineering/mad-harness/harness plugins/engineering/mad-harness/agents plugins/engineering/mad-harness/skills plugins/engineering/mad-harness/commands)" ]; then
  echo "   note: the live tree has uncommitted changes; the series runs $SHA, not them"
fi
export WAVELAB_SKIP_FRESH=1
echo "== A/B $LEVER: arms [$ARMS] x $RUNS run(s), fan-out $FANOUT, backend $BACKEND, root $ROOT, code $SHA"
IFS=',' read -r -a ARM_LIST <<< "$ARMS"
for ARM in "${ARM_LIST[@]}"; do
  [ "$ARM" = "baseline" ] && ARM=off
  # The tree this arm runs: the same one for every lever but `release`.
  ARM_FROZEN="$FROZEN"; ARM_SHA="$SHA"
  if [ "$LEVER" = "release" ] && [ "$ARM" = "off" ]; then ARM_FROZEN="$FROZEN_OFF"; ARM_SHA="$SHA_OFF"; fi
  FLAB="$ARM_FROZEN/plugins/engineering/mad-harness/harness/wavelab"
  FHARNESS="$ARM_FROZEN/plugins/engineering/mad-harness/harness"
  for RUN in $(seq 1 "$RUNS"); do
    RUN_ROOT="$ROOT/$LEVER-$ARM-$RUN"
    if [ -f "$RUN_ROOT/.ab-done" ]; then echo "-- $LEVER:$ARM:$RUN already done, skipping"; continue; fi
    echo; echo "== $LEVER:$ARM:$RUN  ($RUN_ROOT)  code $ARM_SHA"
    rm -rf "$RUN_ROOT"
    "$FLAB/reset.sh" --root "$RUN_ROOT" --only "$BACKEND" --fanout "$FANOUT" --cap "$FANOUT" || {
      echo "!! reset failed for $LEVER:$ARM:$RUN — stopping this lever" >&2; exit 4; }
    # The label every dispatch event in this run carries.
    arm_envs "$ARM"
    ENVS+=("MAD_HARNESS_EXPERIMENT=$LEVER:$ARM:$RUN")
    if [ "$ORCHESTRATED" = "1" ]; then
      LAB_REPO="$RUN_ROOT/$BACKEND"
      # A real remote, so the close-out's push is a push and not a failure the arm pays for.
      git init -q --bare "$RUN_ROOT/remote.git"
      git -C "$LAB_REPO" remote add origin "$RUN_ROOT/remote.git"
      git -C "$LAB_REPO" push -q -u origin HEAD
      git -C "$LAB_REPO" remote set-head origin -a >/dev/null   # origin/HEAD, as a real clone has it
      # THE STAMP THE PRE-FLIGHT DEMANDS. The lab's base config predates `harness.version`
      # because the shell wave scripts never ran pre-flight; a campaign does, and refuses an
      # unstamped config (exit 3) before spending anything. Stamp it with the version of the
      # plugin THIS ARM runs — the same treatment for both arms.
      ARM_VERSION=$(python3 -c "import json;print(json.load(open('$ARM_FROZEN/plugins/engineering/mad-harness/.claude-plugin/plugin.json'))['version'])")
      python3 - "$LAB_REPO/harness.yaml" "$ARM_VERSION" <<'PY'
import re, sys
path, ver = sys.argv[1], sys.argv[2]
text = open(path).read()
if re.search(r"^harness:\s*$", text, re.M):
    if re.search(r"^  version:", text, re.M):
        text = re.sub(r"^(  version:).*$", rf"\1 {ver}", text, count=1, flags=re.M)
    else:
        text = re.sub(r"^(harness:\s*\n)", rf"\1  version: {ver}\n", text, count=1, flags=re.M)
else:
    text = text.rstrip("\n") + f"\n\nharness:\n  version: {ver}\n"
open(path, "w").write(text)
PY
      git -C "$LAB_REPO" -c user.email=wavelab@example.com -c user.name=wavelab commit -q -am "wavelab: stamp harness.version $ARM_VERSION" && git -C "$LAB_REPO" push -q
      EPIC=$(cd "$LAB_REPO" && "$FHARNESS/tracker/tk.sh" list --type epic --json | python3 -c 'import json,sys; r=[t for t in json.load(sys.stdin) if t["status"]!="closed"]; print(r[0]["id"] if r else "")')
      [ -n "$EPIC" ] || { echo "!! no open epic in $LAB_REPO after reset" >&2; exit 4; }
      echo "-- orchestrated: campaign.sh --epic $EPIC from $FHARNESS"
      ( cd "$LAB_REPO" && env "${ENVS[@]}" "$FHARNESS/swarm/campaign.sh" --epic "$EPIC" --max-epics 1 ) || true
      # WHAT THE EPIC CAME TO, from the tracker — the orchestrator judged as it went, so
      # there is no lens-verdicts.txt; the yield is what closed. ab_report reads this.
      ( cd "$LAB_REPO" && "$FHARNESS/tracker/tk.sh" list --json ) | python3 -c '
import json, sys
rows = json.load(sys.stdin)
tasks = [t for t in rows if t["type"] != "epic"]
epics = [t for t in rows if t["type"] == "epic"]
print(json.dumps({"epic": sys.argv[1], "epic_status": next((e["status"] for e in epics if e["id"] == sys.argv[1]), "?"),
                  "tasks": len(tasks), "closed": sum(1 for t in tasks if t["status"] == "closed"),
                  "open": sorted(t["id"] for t in tasks if t["status"] != "closed")}))
' "$EPIC" > "$RUN_ROOT/outcome.json" || true
      cat "$RUN_ROOT/outcome.json" 2>/dev/null || true
    else
    env "${ENVS[@]}" "$FLAB/dispatch-wave.sh" --root "$RUN_ROOT" "$BACKEND" || true
    env "${ENVS[@]}" "$FLAB/merge-wave.sh" --root "$RUN_ROOT" "$BACKEND" || true
    if [ "$WAVE1_ONLY" = "0" ]; then
      env "${ENVS[@]}" "$FLAB/dispatch-wave.sh" --root "$RUN_ROOT" "$BACKEND" || true
      env "${ENVS[@]}" "$FLAB/merge-wave.sh" --root "$RUN_ROOT" "$BACKEND" || true
    fi
    # WHAT THE WORK WAS WORTH, NOT ONLY WHAT IT COST. A lever that makes workers cheaper
    # by making them do less of the doctrine reads as a win on cost alone; the three
    # lenses over every landed task are the measure that catches it. Measured: the arm
    # that carried test-doctrine ran mutation testing 4x as often and cost 61% more.
    if [ "$LENSES" = "1" ]; then
      env "${ENVS[@]}" "$FLAB/lens-wave.sh" --root "$RUN_ROOT" "$BACKEND" || true
    fi
    fi
    # A LIMITED ACCOUNT IS NOT A SAMPLE. When the usage window closes the CLI returns
    # "You've hit your session limit" in one turn at $0 for every dispatch; a series that
    # keeps going records nothing for hours and the runs read as complete. Discard the run
    # and stop the series here; it resumes where it left off once the window reopens.
    EVENTS="$RUN_ROOT/$BACKEND/.harness/run/events/harness.dispatch.jsonl"
    if [ -f "$EVENTS" ] && python3 -c '
import json, sys
rows = [json.loads(l).get("payload", json.loads(l)) for l in open(sys.argv[1]) if l.strip()]
sys.exit(0 if any(r.get("terminal") in ("usage_limit", "api_error") for r in rows) else 1)
' "$EVENTS"; then
      echo "!! $LEVER:$ARM:$RUN hit the account's usage limit (or an API error); discarding the run and STOPPING." >&2
      rm -rf "$RUN_ROOT"
      exit 5
    fi
    echo "$ARM_SHA" > "$RUN_ROOT/.ab-sha"
    date -u +"%Y-%m-%dT%H:%M:%SZ" > "$RUN_ROOT/.ab-done"
  done
done
echo; echo "== done. Read it back:  $HERE/ab-report.sh $LEVER --root $ROOT"
