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
#            worker_provider  off = the shipped worker tier                    on = --worker-tier's provider/model
#            plan_tiers       off = §3 at declared tiers                       on = the READY sanity-check and the audit at strong
#
# TWO CODES, NOT TWO ENVIRONMENTS. Every lever above is one environment variable read by
# the same code; `release` is the one lever where the ARMS ARE DIFFERENT COMMITS — the off
# arm dispatches from a tree frozen at --pin-off, the on arm from HEAD, each running its
# own wavelab scripts. It exists to measure the 0.10.19–0.10.27 series ("the deterministic
# steps out of the prose") against the last release before it.
#
# --plan-only: §3 alone — `plan-epic.sh <epic> --mode auto --triage <what epic-queue says>`
# run directly in the lab repo, no orchestrator, no waves. The instrument for a lever whose
# effect is on planning (plan_tiers): a run is one survey and one sanity-check on a READY
# epic, minutes and about a dollar, so `--runs 3` is affordable where a full epic is not.
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
PIN_OFF="e2fa8d5d1226"; ORCHESTRATED=0; PLAN_ONLY=0; WORKER_TIER=""
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
    --worker-tier) WORKER_TIER="${2:?--worker-tier needs a yaml fragment}"; shift ;;
    --orchestrated) ORCHESTRATED=1 ;;
    --plan-only) ORCHESTRATED=1; PLAN_ONLY=1 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
case "$LEVER" in cache_ttl|static_prefix|stagger|task_budget|preload|lean_catalog|release|plan_tiers|worker_provider) ;; *) echo "unknown lever: $LEVER" >&2; exit 2 ;; esac

# THE ARM IS A TIER REDEFINITION IN THE LAB REPO'S OWN harness.yaml — the project-owned
# model config (0.10.30), not an environment variable, because that is how a consuming
# project would really do it. The `on` arm's block is given as one argument so the rig
# never hard-codes a provider, a model or a price:
#
#   ab.sh worker_provider --lenses --worker-tier 'provider: deepseek
#     model: deepseek-v4-pro
#     price: {input_per_mtok: 1.32, output_per_mtok: 3.96, cache_read_per_mtok: 0.044,
#             off_peak_multiplier: 0.5, peak_utc: ["01:00-04:00", "06:00-10:00"]}'
#
# A tier off Anthropic MUST carry its price or the config check refuses it: the CLI prices
# a third-party endpoint from its own table (measured: $5.00/Mtok for DeepSeek against
# $0.66-1.32 published), and every event then says `cost_source: priced (...)` so the two
# arms are compared on real money rather than on one real number and one estimate.

# The environment each arm dispatches under. Everything else is inherited unchanged, and
# every arm clears the levers it does not set, so a stray export cannot leak into an arm.
ENVS=()
arm_envs() {  # $1 = off|on  -> fills ENVS
  ENVS=(MAD_HARNESS_CACHE_TTL= MAD_HARNESS_STATIC_PREFIX= MAD_HARNESS_STAGGER_SECONDS=
        MAD_HARNESS_TASK_BUDGET_TOKENS= MAD_HARNESS_PRELOAD= MAD_HARNESS_PLAN_TIERS=)
  case "$LEVER:$1" in
    cache_ttl:on)      ENVS+=(MAD_HARNESS_CACHE_TTL=5m) ;;
    static_prefix:on)  ENVS+=(MAD_HARNESS_STATIC_PREFIX=1) ;;
    stagger:off)       ENVS+=(MAD_HARNESS_STATIC_PREFIX=1) ;;
    stagger:on)        ENVS+=(MAD_HARNESS_STATIC_PREFIX=1 MAD_HARNESS_STAGGER_SECONDS=8) ;;
    task_budget:on)    ENVS+=(MAD_HARNESS_TASK_BUDGET_TOKENS=400000) ;;
    preload:on)        ENVS+=(MAD_HARNESS_PRELOAD=evidence-gathering) ;;
    lean_catalog:off)  ENVS+=(MAD_HARNESS_LEAN_CATALOG=0) ;;
    lean_catalog:on)   ENVS+=(MAD_HARNESS_LEAN_CATALOG=1) ;;
    plan_tiers:on)     ENVS+=(MAD_HARNESS_PLAN_TIERS=1) ;;
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
# CREDENTIALS FOLLOW THE FROZEN CODE. `harness/.env` is gitignored, so a worktree of the
# plugin has none and `provider_env` would report every variable missing — the arm would
# run with no provider auth and read as the provider failing. Copied, never committed.
for d in "$FROZEN" "$FROZEN_OFF"; do
  live="$AIM/plugins/engineering/mad-harness/harness/.env"
  [ -f "$live" ] && cp "$live" "$d/plugins/engineering/mad-harness/harness/.env"
done
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
    # The `on` arm's worker tier, written into the lab repo as a project would write it.
    if [ "$LEVER" = "worker_provider" ] && [ "$ARM" = "on" ]; then
      [ -n "$WORKER_TIER" ] || { echo "!! worker_provider needs --worker-tier '<yaml>'" >&2; exit 2; }
      { echo; echo "tiers:"; echo "  worker:"; printf '%s\n' "$WORKER_TIER" | sed 's/^/    /'; } >> "$RUN_ROOT/$BACKEND/harness.yaml"
      ( cd "$RUN_ROOT/$BACKEND" && "$FHARNESS/checks/check-project-config.sh" >/dev/null ) || {
        echo "!! the arm's tier is not a valid config:" >&2
        ( cd "$RUN_ROOT/$BACKEND" && "$FHARNESS/checks/check-project-config.sh" ) >&2; exit 4; }
      ( cd "$RUN_ROOT/$BACKEND" && git -c user.email=wavelab@example.com -c user.name=wavelab commit -qam "wavelab: the arm's worker tier" )
      echo "-- arm tier: $(cd "$RUN_ROOT/$BACKEND" && "$FHARNESS/checks/check-project-config.sh" | grep '^models:' | head -1)"
    fi
    # The label every dispatch event in this run carries.
    arm_envs "$ARM"
    ENVS+=("MAD_HARNESS_EXPERIMENT=$LEVER:$ARM:$RUN")
    if [ "$ORCHESTRATED" = "1" ]; then
      LAB_REPO="$RUN_ROOT/$BACKEND"
      EPIC=$(cd "$LAB_REPO" && "$FHARNESS/tracker/tk.sh" list --type epic --json | python3 -c 'import json,sys; r=[t for t in json.load(sys.stdin) if t["status"]!="closed"]; print(r[0]["id"] if r else "")')
      [ -n "$EPIC" ] || { echo "!! no open epic in $LAB_REPO after reset" >&2; exit 4; }
      # WHAT AN OWNER WOULD HAVE ANSWERED. The seeded epic contradicts itself — task A
      # lowercases the address, the base test asserts `out == raw` with "A@B.com" — and
      # says nothing about non-str values; an architect that reads carefully raises both as
      # DECISION: lines and the hard line parks the epic at §3b (measured: the first run
      # that got that far). Settled here, on the epic, as an owner does — the same words
      # for both arms, so neither arm is measured on a park the other could not avoid.
      ( cd "$LAB_REPO" && "$FHARNESS/tracker/tk.sh" note "$EPIC" "OWNER DECISION (wavelab, pre-answered), VERBATIM: 'Two things the tasks leave open, settled now. (1) tests/test_contact.py: its fixture may be changed to already-normalised values (a@b.com, +61400000000) so that its copy-not-the-original assertion still holds; the criterion \"still passes unchanged\" means that assertion, not the literal fixture. (2) Non-str values for email or phone (an int, a list) pass through clean_contact unchanged; only str values are normalised, and None becomes the empty string as the normalisers already say.' WHAT THIS SETTLES: the fixture and the non-str contract. Do not raise either as a DECISION." >/dev/null )
      # A real remote, so the close-out's push is a push and not a failure the arm pays for.
      # INSIDE THE REPOSITORY (gitignored), because the orchestrator pushes from inside its
      # sandbox, whose write set is the checkout and the caches: a remote beside the run
      # root was refused — "unable to create temporary object directory" — on the first run.
      REMOTE="$LAB_REPO/.harness/run/remote.git"
      mkdir -p "$LAB_REPO/.harness/run" && git init -q --bare "$REMOTE"
      git -C "$LAB_REPO" remote add origin "$REMOTE"
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
      # An explicit export: the backend's auto-export after the note is not synchronous, and
      # a commit that raced it left issues.jsonl modified — which pre-flight refuses (measured).
      ( cd "$LAB_REPO" && "$FHARNESS/tracker/tk.sh" export >/dev/null 2>&1 || true )
      git -C "$LAB_REPO" add -A
      git -C "$LAB_REPO" -c user.email=wavelab@example.com -c user.name=wavelab commit -q -m "wavelab: stamp harness.version $ARM_VERSION; the owner's settlement on the epic" && git -C "$LAB_REPO" push -q
      [ -z "$(git -C "$LAB_REPO" status --porcelain)" ] || { echo "!! lab repo dirty after the seed commit:"; git -C "$LAB_REPO" status --porcelain; exit 4; }
      if [ "$PLAN_ONLY" = "1" ]; then
        # §3 alone. Pre-flight first, as the campaign would (autosync off, the tree clean),
        # with the triage the queue computes for this epic — READY once the seed carries
        # acceptance in the field.
        ( cd "$LAB_REPO" && "$FHARNESS/swarm/preflight.sh" >/dev/null ) || { echo "!! pre-flight refused the lab repo"; ( cd "$LAB_REPO" && "$FHARNESS/swarm/preflight.sh" ); exit 4; }
        TRIAGE=$(cd "$LAB_REPO" && "$FHARNESS/swarm/epic-queue.sh" --json | python3 -c 'import json,sys; d=json.load(sys.stdin); e=[x for x in d.get("epics",[]) if x["id"]==sys.argv[1]]; print((e[0].get("triage") or "UNPLANNED") if e else "UNPLANNED")' "$EPIC")
        echo "-- plan-only: plan-epic.sh $EPIC --mode auto --triage $TRIAGE from $FHARNESS"
        ( cd "$LAB_REPO" && env "${ENVS[@]}" "$FHARNESS/swarm/plan-epic.sh" "$EPIC" --mode auto --triage "$TRIAGE" ) || true
        ( cd "$LAB_REPO" && "$FHARNESS/tracker/tk.sh" autosync on >/dev/null 2>&1 ) || true
      else
      echo "-- orchestrated: campaign.sh --epic $EPIC from $FHARNESS"
      ( cd "$LAB_REPO" && env "${ENVS[@]}" "$FHARNESS/swarm/campaign.sh" --epic "$EPIC" --max-epics 1 ) || true
      fi
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
