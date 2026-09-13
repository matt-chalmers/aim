#!/usr/bin/env bash
# Dispatch one gate agent at a fixture built to trip it, and check what came back.
#
#   harness/wavelab/stage-lab.sh [--root DIR] <stage|all>
#
# The wave test proves the plumbing. This tests the GATES — an epic that should be parked,
# a test that should be failed, a plan that should be serialised. A gate that never fires
# is indistinguishable from one that always passes, and only a fixture built to fail it
# tells the two apart.
set -uo pipefail

# Record where we were invoked from. This script reaches into the harness via a
# subshell or a computed path, so Python still runs with the harness as its cwd —
# and the harness carries its own harness.yaml, which the resolver would read as
# the project. Exported here so every subshell inherits it.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"

ROOT="${WAVELAB_ROOT:-$HOME/harness-wavelab}"
while [ "${1:-}" = "--root" ]; do ROOT="${2:?}"; shift 2; done
STAGE="${1:?usage: stage-lab.sh [--root DIR] <stage|all>}"

HERE="$(cd "$(dirname "$0")" && pwd)"
HARNESS="$(cd "$HERE/.." && pwd)"
REPO="$ROOT/stages"
SCRATCH="${SCRATCHPAD:-${TMPDIR:-/tmp}}/wavelab-stages"
mkdir -p "$SCRATCH"

# A throwaway repo per stage: the fixtures contradict each other by design, so they cannot
# share one tree.
build_repo() {
  rm -rf "$REPO"; mkdir -p "$REPO"
  cp -R "$HERE/base/." "$REPO/"
  printf 'name: Stages\nslug: stages\nareas: [{path: src, label: code}]\npaths: {docs: docs, proposed: docs/proposed}\nstacks: [python-uv]\nlanes: {backend: {stacks: [python-uv], agent: fullstack-engineer, cap: 2}}\ntracker: {backend: mdfiles, dir: .harness/tasks, export: docs/tasks, limits: {record_bytes: null}}\n' \
    > "$REPO/harness.yaml"
  ( cd "$REPO" && uv lock --quiet && uv sync --quiet && git init -q . && git add -A \
    && git -c user.email=w@e -c user.name=w commit -q -m base )
}

tk() { MAD_HARNESS_REPO="$REPO" "$HARNESS/tracker/tk.sh" "$@"; }

dispatch() {  # $1 agent  $2 prompt-file  -> prints the returned text
  MAD_HARNESS_REPO="$REPO" "$HARNESS/models/dispatch.sh" "$1" \
    --prompt-file "$2" --no-record 2>"$SCRATCH/err-$1.txt"
}

# $1 = label, $2 = returned text, $3.. = regexes that must ALL appear
expect() {
  local label="$1" text="$2"; shift 2
  local ok=1 pat
  for pat in "$@"; do
    printf '%s' "$text" | grep -qiE "$pat" || { ok=0; echo "    MISSING: /$pat/"; }
  done
  if [ "$ok" = 1 ]; then echo "  PASS  $label"; else
    echo "  FAIL  $label"; echo "$text" | head -12 | sed 's/^/        /'; FAILURES=$((FAILURES+1))
  fi
}

FAILURES=0
run_stage() { case "$1" in
  adequacy-absent) stage_adequacy_absent ;;
  adequacy-ok)     stage_adequacy_ok ;;
  lens-tests)      stage_lens_tests ;;
  lens-correctness) stage_lens_correctness ;;
  lens-spec)       stage_lens_spec ;;
  plan-contention) stage_plan_contention ;;
  *) echo "unknown stage: $1" >&2; exit 2 ;;
esac }

. "$HERE/stages/definitions.sh"

if [ "$STAGE" = "all" ]; then
  for s in adequacy-absent adequacy-ok lens-tests lens-correctness lens-spec plan-contention; do run_stage "$s"; done
else
  run_stage "$STAGE"
fi
echo
[ "$FAILURES" = 0 ] && echo "all stages behaved as their gate requires" \
  || echo "$FAILURES stage(s) did not fire as required"
exit "$FAILURES"
