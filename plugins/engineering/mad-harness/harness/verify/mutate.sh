#!/usr/bin/env bash
# Run a mutation batch in a tree that cannot carry stale bytecode and cannot be
# corrupted by a concurrent agent.
#
#   harness/verify/mutate.sh <task-id> <commit-ish> <mutations-file> [test-ids...]
#
# Two traps this repo has paid for, both silent, both structurally closed here
# rather than left as something to remember:
#
#   1. STALE .pyc MIS-ATTRIBUTES RESULTS. rsync -a preserves __pycache__ with
#      matching mtime/size, so a copied tree reuses stale .pyc and a mutant can
#      execute PRE-mutation bytecode — reading as "survived" when it was never
#      applied. An in-place harness fails the other way: on one recorded task a
#      one-line deletion reported 13 failed inside a sequential run and 1 failed
#      standalone; the extra 12 were the PREVIOUS mutant's bytecode.
#      CLOSED BY: `git archive` into a fresh dir (a tree that never had a
#      __pycache__ cannot have a stale one) + PYTHONDONTWRITEBYTECODE=1.
#
#   2. SCRATCHPAD COLLISION BETWEEN CONCURRENT AGENTS. Generic names collide in
#      the shared scratchpad root — pristine/, vt/, the runner env, and once mutate.py
#      itself, so one agent's `restore` ran the OTHER agent's script against the
#      OTHER agent's worktree while its own tree stayed mutated.
#      CLOSED BY: every path derived from <task-id>, and the caller's worktree is
#      never written to at all — we mutate a throwaway copy.
#
# It also refuses to report a number it cannot justify:
#   - a mutation that does not match EXACTLY ONCE aborts (a mutant that never
#     applied must never read as "survived")
#   - restore is a RE-EXTRACT, not an undo, so a failed restore is impossible
#   - it records failing test NAMES per mutant, not just a kill count, because a
#     kill count alone never proves the mutant was killed for the right reason
#   - it re-runs the first mutant last and aborts if the result moved
#
# Mutations file: one per line, four fields separated by a literal | —
#   name|path/relative/to/repo/root.py|exact old text|exact new text
# Blank lines and # comments ignored. Delete-a-line is new-text empty.
set -euo pipefail

# Record where we were invoked from. This script reaches into the harness via a
# subshell or a computed path, so Python still runs with the harness as its cwd —
# and the harness carries its own harness.yaml, which the resolver would read as
# the project. Exported here so every subshell inherits it.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"

TASK="${1:?usage: harness/verify/mutate.sh <task-id> <commit-ish> <mutations-file> [test-ids...]}"
REF="${2:?missing <commit-ish>}"
MUTFILE="${3:?missing <mutations-file>}"
shift 3
TEST_ARGS=("$@")

# THE RUNNER COMES FROM THE STACK MODULE, NOT FROM THIS SCRIPT.
# Mutation testing has to execute a suite, so it needs a runner — but which one is a
# property of the project's toolchain, not of the technique. `MUTATE_STACK` names the
# stack whose commands to use; it defaults to the first stack that declares a scoped
# test command.
# Resolved by models/commands.py, which also owns the root-relative cwd — a stack
# living in services/api runs its suite there, not at the repository root. Reading
# `commands.cwd` directly (as this script used to) drops that prefix silently.
_stack_cmd() {  # $1 = command key
  ( cd "$(dirname "${BASH_SOURCE[0]}")/.." \
      && env -u VIRTUAL_ENV uv run python -m models.commands --print ${MUTATE_STACK:+--stack "$MUTATE_STACK"} "$@" 2>/dev/null )
}
_stack_cfg() {  # $1 = a python snippet printing to stdout
  ( cd "$(dirname "${BASH_SOURCE[0]}")/.." && env -u VIRTUAL_ENV uv run python -c "$1" 2>/dev/null )
}
_cmd_lines="$(_stack_cmd test_scoped)"

# How this project's runner reports. Defaults are pytest's, so a pytest project is
# unchanged; anything else declares `test_output` in its stack module. Line 216 is why
# this matters: a runner that prints a matching summary while naming failures
# differently would report every mutant SURVIVED — confident, silent, and wrong, which
# the header above calls the most dangerous output this script can produce.
SUMMARY_RE="$(_stack_cfg "
from models.project import load
for s in load().stacks:
    o = s.raw.get('test_output') or {}
    if o.get('summary'):
        print(o['summary']); break
else:
    print(r'^(=+ )?[0-9]+ (passed|failed)[^|]* in [0-9]')")"
FAILURE_RE="$(_stack_cfg "
from models.project import load
for s in load().stacks:
    o = s.raw.get('test_output') or {}
    if o.get('failure_line'):
        print(o['failure_line']); break
else:
    print('^FAILED [^ ]+')")"
FAILURE_STRIP="$(_stack_cfg "
from models.project import load
for s in load().stacks:
    o = s.raw.get('test_output') or {}
    if o.get('failure_strip'):
        print(o['failure_strip']); break
else:
    print('^FAILED ')")"
: "${SUMMARY_RE:=^(=+ )?[0-9]+ (passed|failed)[^|]* in [0-9]}"
: "${FAILURE_RE:=^FAILED [^ ]+}"
: "${FAILURE_STRIP:=^FAILED }"
TEST_CMD="$(printf '%s' "$_cmd_lines" | sed -n 1p)"
TEST_CWD="$(printf '%s' "$_cmd_lines" | sed -n 2p)"
if [ -z "${TEST_CMD:-}" ]; then
  echo "FATAL: no stack declares a scoped test command — mutation testing cannot run." >&2
  echo "       Add commands.test_scoped to a stack module, or set MUTATE_STACK." >&2
  exit 3
fi
# The runner takes explicit test ids, so strip any {path} placeholder.
TEST_CMD="${TEST_CMD/\{path\}/}"
# NO DEFAULT SCOPE. The old default was `-q`, which runs the ENTIRE suite for EVERY mutant —
# 120s each instead of 2.3s. A lens burned 96 minutes that way on 2026-08-27.
# test-doctrine §5: scope a targeted mutant to the tests that should catch it.
#
# The FIRST version of this guard did not fire: it looped over "${TEST_ARGS[@]:-}", which
# expands to one EMPTY STRING when the array is empty, and an empty string matches the
# not-a-flag branch. So the zero-argument case — the exact one it existed for — set
# _has_path=1 and sailed through, with the -q default already removed. Check the count first.
_scope_fatal() {
  echo "FATAL: no test path given, so every mutant would run the WHOLE suite (~120s each)." >&2
  echo "       Scope it — name individual tests — per test-doctrine §5. ~2.3s each." >&2
  echo "       The ONLY exception is the class-1 deletion mutant; pass a broad path" >&2
  echo "       explicitly (e.g. two specific package paths) to say you meant it." >&2
  exit 2
}
[ "${#TEST_ARGS[@]}" -eq 0 ] && _scope_fatal
_has_path=0
_skip_next=0
for _a in "${TEST_ARGS[@]}"; do
  if [ "$_skip_next" = "1" ]; then _skip_next=0; continue; fi   # -k EXPR / -m EXPR: the value is not a path
  case "$_a" in
    -k|-m|-p|--deselect|--ignore) _skip_next=1 ;;
    -*) ;;
    *) _has_path=1 ;;
  esac
done
[ "$_has_path" = "0" ] && _scope_fatal

MAIN="$(git rev-parse --show-toplevel)"
MAIN="$(git -C "$MAIN" worktree list --porcelain | awk '/^worktree /{print $2; exit}')"
SLUG="$(printf '%s' "$TASK" | tr -c 'A-Za-z0-9' '-' | tr -s '-' | sed 's/^-//;s/-$//')"
# INSIDE THE CHECKOUT BY DEFAULT. Under `/tmp` a sandboxed worker could not read its own
# mutations file back (measured: "Path is outside allowed working directories"), and the
# doctrine's "with SCRATCHPAD set" had workers prefixing this call with `env SCRATCHPAD=…`,
# which no rule matches. `.harness/run/` is gitignored and always writable.
ROOT="${SCRATCHPAD:-$PWD/.harness/run/mut}/${SLUG}-mut"
TREE="$ROOT/tree"
LOG="$ROOT/${SLUG}-mutants.txt"

command -v git >/dev/null || { echo "git not found" >&2; exit 2; }
[ -f "$MUTFILE" ] || { echo "no such mutations file: $MUTFILE" >&2; exit 2; }
# RESOLVE THE REF WHERE THE CALLER IS STANDING, NOT IN THE PRIMARY CHECKOUT.
# $MAIN is deliberately the primary worktree (its object store holds every commit, including
# those made in linked worktrees, so `git archive` can always find them). But RESOLVING the ref
# there is wrong: from a swarm worktree, `HEAD` would silently mean the PRIMARY checkout's HEAD,
# the caller's own commit would be absent from the extracted tree, and every mutant would read
# as SURVIVED against code that never contained the change. That is the exact false-survival
# this script exists to prevent, and it nearly landed on a real task — the only tell was a
# run reporting 24 tests in a tree that had 27.
SHA="$(git rev-parse --verify "${REF}^{commit}")" || {
  echo "FATAL: cannot resolve '$REF' from $(pwd)" >&2; exit 2; }
if [ "${ALLOW_DANGLING:-0}" != "1" ] && [ -z "$(git -C "$MAIN" branch --contains "$SHA" 2>/dev/null | head -1)" ]; then
  echo "FATAL: $REF resolves to ${SHA:0:12}, which NO BRANCH CONTAINS." >&2
  echo "       It was probably amended or rebased away — a worker commonly does this after" >&2
  echo "       a lens finding. Measuring it produces a verdict on code nobody will merge." >&2
  echo "       Happened twice on 2026-08-27 (ef2d1b8, ec69fbf). Re-resolve against the" >&2
  echo "       branch tip, or set ALLOW_DANGLING=1 if you truly mean this commit." >&2
  exit 2
fi
CALLER_ROOT="$(git rev-parse --show-toplevel)"
if [ "$CALLER_ROOT" != "$MAIN" ]; then
  echo "    NOTE: called from linked worktree $CALLER_ROOT"
  echo "          '$REF' resolved THERE -> ${SHA:0:12} (not the primary checkout's HEAD)"
fi
git -C "$MAIN" cat-file -e "${SHA}^{commit}" 2>/dev/null || {
  echo "FATAL: $SHA is not reachable from the primary object store at $MAIN." >&2
  echo "       Commit your work before mutating — an unreachable ref cannot be archived." >&2
  exit 2; }

echo "==> mutation batch for ${TASK}"
echo "    ref:        ${REF} (${SHA:0:12})"
echo "    isolated:   ${ROOT}"
echo "    caller tree: NEVER WRITTEN TO"

mkdir -p "$ROOT"

rebuild_tree() {
  rm -rf "$TREE"; mkdir -p "$TREE"
  git -C "$MAIN" archive "$SHA" | tar -x -C "$TREE"
  # .venv and node_modules are gitignored so archive cannot carry them; link the
  # venv rather than copying 235MB per mutant.
  # Dependency directories are gitignored so archive cannot carry them; link each
  # stack's rather than copying hundreds of megabytes per mutant.
  while read -r _dep; do
    [ -n "$_dep" ] && [ -e "$MAIN/$_dep" ] && mkdir -p "$(dirname "$TREE/$_dep")" \
      && ln -sfn "$MAIN/$_dep" "$TREE/$_dep"
  done < <(cd "$(dirname "${BASH_SOURCE[0]}")/.." && env -u VIRTUAL_ENV uv run python -c \
    "from models.project import load; [print(s.dependency_dir) for s in load().stacks]" 2>/dev/null)
  # Belt and braces: archive cannot produce these, but assert it.
  if find "$TREE" -name '__pycache__' -type d | grep -q .; then
    echo "FATAL: __pycache__ present in a freshly archived tree — abort" >&2; exit 3
  fi
}

apply_mutation() {  # <file> <old> <new>
  python3 - "$TREE/$1" "$2" "$3" <<'PY'
import sys, pathlib
p, old, new = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
if not p.exists():
    sys.exit(f"FATAL: {p} does not exist in the archived tree")
s = p.read_text()
n = s.count(old)
if n != 1:
    sys.exit(f"FATAL: mutation text matched {n} times in {p.name}, expected exactly 1. "
             "A mutant that did not apply must never read as 'survived'.")
p.write_text(s.replace(old, new, 1))
PY
}

run_scoped() {  # -> prints "<count>|<space-separated failing test ids>"; ABORTS if the suite did not run
  # The isolating variable is whatever the stacks declare, not DB_NAME. A project that
  # isolates workers differently was hard-failing here before running anything.
  _iso="$(_stack_cfg "
from models.project import load
print(' '.join(sorted(load().worker_env(1))))")"
  for _v in $_iso; do
    if [ -z "${!_v:-}" ]; then
      echo "FATAL: $_v is unset. Source .swarm-env first — a mutation run that shares" >&2
      echo "       another worker's resources produces plausible wrong counts." >&2
      exit 3
    fi
  done
  # Every path here is derived from the task id, and the isolating variables must be
  # too, or two concurrent agents deadlock on a shared resource. That has happened —
  # the rule existed in prose and was followed by neither dispatch.
  #
  # Loops over whatever the stacks declare. It named DB_NAME directly until an
  # adversarial run hit `DB_NAME: unbound variable` here: the isolation CHECK above was
  # converted to read the config and this WARNING beneath it was not, so the script
  # aborted under `set -u` in any project that isolates by another variable.
  for _v in $_iso; do
    case "${!_v}" in
      *"${TASK##*-}"*) ;;
      *) echo "WARNING: $_v='${!_v}' does not contain '${TASK##*-}'. Two concurrent" >&2
         echo "         agents sharing one resource deadlock and produce plausible" >&2
         echo "         wrong counts. Derive it from the task id." >&2;;
    esac
  done
  local out rc
  out="$(cd "$TREE/$TEST_CWD" && PYTHONDONTWRITEBYTECODE=1 \
        $TEST_CMD "${TEST_ARGS[@]}" 2>&1)" && rc=0 || rc=$?
  printf '%s\n' "$out" > "$ROOT/last-run.txt"

  # A MUTANT THAT "SURVIVED" MUST BE DISTINGUISHABLE FROM A SUITE THAT NEVER RAN.
  # Convention: 0 = all passed, 1 = tests failed. Anything else (2 interrupted, 3 internal,
  # 4 usage, 5 nothing collected) means we have no evidence either way — and a zero-red
  # reading from those is the most dangerous output this script can produce, because it
  # reads as "no test asserts this behaviour".
  if [ "$rc" -ne 0 ] && [ "$rc" -ne 1 ]; then
    echo "FATAL: the runner exited $rc — the suite did not run to completion, so this mutant's" >&2
    echo "       result is NOT EVIDENCE. Last 20 lines:" >&2
    printf '%s\n' "$out" | tail -20 >&2
    exit 6
  fi
  # Belt and braces: require an actual summary line. A collection error can still exit 1.
  # Accept BOTH common summary forms. Verbose prints a banner
  #   ===== 1 failed, 18 passed in 0.56s =====
  # while -q prints it bare
  #   1 failed, 18 passed in 0.56s
  # The script's own default is -q, so requiring the banner aborted every default
  # invocation, once, for real. What the guard must still reject is a run with NO
  # summary at all — a collection error, an import failure, a killed process — so
  # both alternatives are anchored and require the trailing " in <time>" that only
  # a completed run prints.
  if ! printf '%s\n' "$out" | grep -qE '^(=+ )?[0-9]+ (passed|failed)[^|]* in [0-9]'; then
    echo "FATAL: no test summary line — the suite did not report. Result is NOT EVIDENCE." >&2
    printf '%s\n' "$out" | tail -20 >&2
    exit 6
  fi

  local names count
  names="$(printf '%s\n' "$out" | grep -oE '^FAILED [^ ]+' | sed 's/^FAILED //' | sort | tr '\n' ' ')"
  count="$(printf '%s\n' "$names" | tr ' ' '\n' | grep -c . || true)"
  printf '%s|%s' "$count" "$names"
}

: > "$LOG"
declare -a NAMES OLDS NEWS FILES
while IFS='|' read -r n f o w || [ -n "${n:-}" ]; do   # `|| [ -n ]` keeps a file with no trailing newline
  [ -z "${n:-}" ] && continue
  case "$n" in \#*) continue;; esac
  if [ -z "${f:-}" ] || [ -z "${o:-}" ]; then
    echo "FATAL: malformed mutation line (need name|path|old|new): $n" >&2; exit 2
  fi
  NAMES+=("$n"); FILES+=("$f"); OLDS+=("$o"); NEWS+=("${w:-}")
done < "$MUTFILE"
[ ${#NAMES[@]} -gt 0 ] || { echo "no mutations parsed from $MUTFILE" >&2; exit 2; }

echo "    mutants:    ${#NAMES[@]}"
echo

FIRST_RESULT=""
for i in "${!NAMES[@]}"; do
  rebuild_tree                       # restore is a RE-EXTRACT: cannot fail
  apply_mutation "${FILES[$i]}" "${OLDS[$i]}" "${NEWS[$i]}"
  r="$(run_scoped)"; c="${r%%|*}"; ids="${r#*|}"
  [ -z "$FIRST_RESULT" ] && FIRST_RESULT="$r"
  printf '%-28s %3s red  %s\n' "${NAMES[$i]}" "$c" "$ids" | tee -a "$LOG"
  [ "$c" -eq 0 ] && printf '  ^^ SURVIVED — no test asserts this behaviour\n' | tee -a "$LOG"
done

# Re-run mutant 1 last. A moved result means the run was contaminated and every
# number above is suspect (see the recorded sequential-run incident).
echo
rebuild_tree
apply_mutation "${FILES[0]}" "${OLDS[0]}" "${NEWS[0]}"
RECHECK="$(run_scoped)"
if [ "$RECHECK" != "$FIRST_RESULT" ]; then
  echo "FATAL: re-running mutant 1 gave a DIFFERENT result." | tee -a "$LOG"
  echo "  first:  ${FIRST_RESULT}" | tee -a "$LOG"
  echo "  recheck:${RECHECK}"      | tee -a "$LOG"
  echo "  Every number in this batch is suspect. Do not report them." | tee -a "$LOG"
  exit 4
fi
echo "recheck of mutant 1 reproduced exactly — batch is trustworthy" | tee -a "$LOG"
echo
echo "==> log: $LOG"
rm -rf "$TREE"
