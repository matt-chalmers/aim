#!/usr/bin/env bash
# Run the verification lenses over tasks that have already landed.
#
#   harness/wavelab/lens-wave.sh [--root DIR] <beads|mdfiles> [task-id ...]
#
# Separate from dispatch-wave.sh because judging is not dispatching: a task can be
# re-judged without rebuilding the repo or spending another wave, which is what makes the
# lens path testable at all. dispatch-wave.sh --lens calls straight into this.
#
# L1 (`verifier`) reads the per-file patches. L3 (`verifier-spec`) reads brief.md and the
# repository at HEAD and MUST NOT see the diff. Running both is what exercises the
# artefact split verify/brief.py exists to produce.
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

# No ids given: judge every closed task. The lens is for work that has landed.
if [ "${#TASKS[@]}" -eq 0 ]; then
  mapfile -t TASKS < <(tk list --json | python3 -c '
import json, sys
for t in json.load(sys.stdin):
    if t["type"] != "epic" and t["status"] == "closed":
        print(t["id"])')
fi
[ "${#TASKS[@]}" -gt 0 ] || { echo "nothing closed to judge"; exit 0; }

echo "== verification lenses: $NAME =="
VERDICTS="$SCRATCH/verdicts.txt"; : > "$VERDICTS"

for TASK in "${TASKS[@]}"; do
  SHA=$(cd "$REPO" && git log --all --oneline --grep="$TASK" -1 --format=%H || true)
  [ -n "$SHA" ] || { echo "  $TASK: no commit found, skipping"; continue; }

  # KEEP THE STREAMS APART. brief.sh prints the path on stdout and its size comparison on
  # stderr; merging them and taking head -1 hands the lens the size note, because stdout
  # block-buffers into a file and stderr does not.
  BRIEF=$("$HARNESS/verify/brief.sh" "$TASK" "$SHA" 2> "$SCRATCH/brief-$TASK.err" | head -1) || true
  if [ ! -f "$BRIEF" ]; then
    echo "  $TASK: no brief produced — $(tail -1 "$SCRATCH/brief-$TASK.err" 2>/dev/null)"
    continue
  fi
  echo "-- $TASK  brief: $(sed -n '1s/^-- //p' "$SCRATCH/brief-$TASK.err")"

  printf 'Judge task %s for CORRECTNESS. Its brief is at %s. Read the brief and the\nper-file patches under its diff/ directory. Return VERDICT: PASS or FAIL on the\nfirst line, then located findings.\n' \
    "$TASK" "$BRIEF" > "$SCRATCH/lens1-$TASK.txt"
  # NO REPO PATH IN THE PROMPT. Handing an agent an absolute path invites `cd <path>`
  # and `git -C <path>`, both of which are denied — and it is already IN the repo.
  printf 'Judge task %s against the SPEC and the repository at HEAD, which is your\nworking directory. Read %s — the brief BODY only. Do NOT open its diff/\ndirectory: you are judging what the repository now claims, not how it changed.\nCheck docs, docstrings and callers for anything the change left contradicted.\nReturn VERDICT: PASS or FAIL on the first line, then located findings.\n' \
    "$TASK" "$BRIEF" > "$SCRATCH/lens3-$TASK.txt"

  for AGENT in verifier verifier-spec; do
    case "$AGENT" in
      verifier) LP="$SCRATCH/lens1-$TASK.txt"; LABEL="L1 correctness" ;;
      *)        LP="$SCRATCH/lens3-$TASK.txt"; LABEL="L3 spec" ;;
    esac
    OUT="$SCRATCH/${AGENT}-$TASK.txt"
    "$HARNESS/models/dispatch.sh" "$AGENT" --prompt-file "$LP" --task "$TASK" \
      > "$OUT" 2>&1 || true
    V=$(grep -oiE '\bVERDICT:?[[:space:]]*(PASS|FAIL)' "$OUT" | head -1 \
        | grep -oiE '(PASS|FAIL)' | tr 'a-z' 'A-Z')
    printf '  %-16s %-14s %s\n' "$TASK" "$LABEL" "${V:-<no verdict>}" | tee -a "$VERDICTS"
  done
done

echo
echo "== verdicts =="
cat "$VERDICTS"
echo "full lens output: $SCRATCH"
