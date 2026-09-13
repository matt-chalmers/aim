#!/usr/bin/env bash
# check-doc-drift.sh <base-ref> [head-ref]
#
# WHY THIS EXISTS. In one unattended campaign wave, THREE of five lens FAILs were
# the same shape: a change edited one statement of a rule and left every OTHER statement of
# that rule untouched, turning a file that was consistently incomplete into one that is
# self-contradictory (memory a-half-applied-doc-correction-is-worse-than).
#
#   - a task amended a decision record and left a feature doc's acceptance criterion
#     asserting the opposite of the record it cites
#     — and no task in the wave owned that file.
#   - another rewrote a criterion into three refusal limbs and left a neighbouring field
#     note describing the old single limb.
#
# Each cost a remediation dispatch plus a re-lens — roughly 150k-200k subagent tokens. Each
# is a grep. This script is that grep.
#
# It is a REPORTING tool, not a gate: it cannot know which hits are stale. It answers
# "where else does the corpus talk about what you just changed?" and makes NOT looking a
# deliberate choice rather than an oversight.

set -uo pipefail
BASE="${1:?usage: check-doc-drift.sh <base-ref> [head-ref]}"
HEAD_REF="${2:-HEAD}"
PWD_REPO="${MAD_HARNESS_REPO:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
export PWD_REPO
cd "$PWD_REPO"

# PATHSPECS COME FROM CONFIG. They were `docs/**` `backend/**` `frontend/**`, which
# match nothing in any other layout — so this check printed "no docs or source files
# changed" and exited 0 on every repository but one. An empty result reading as a clean
# result is the failure three sibling scripts already guard against; this was the one
# the earlier pass missed.
_cfg() { ( cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")/.." \
           && MAD_HARNESS_REPO="$PWD_REPO" uv run python -c "$1" 2>/dev/null ); }
DOCS_DIR="$(_cfg "from models.project import load; print(load().paths.get('docs') or 'docs')")"
ADRS_DIR="$(_cfg "from models.project import load; print(load().paths.get('adrs') or '')")"
AREA_SPECS="$(_cfg "from models.project import load; print(' '.join(a.path for a in load().areas))")"
: "${AREA_SPECS:=$DOCS_DIR}"
DOC_SPEC="$DOCS_DIR/**/*.md"
if [ -z "${AREA_SPECS// /}" ]; then
  echo "harness.yaml declares no areas — cannot tell which paths are source." >&2
  exit 1
fi

# STAGED AND GENERATED FILES ARE EXCLUDED, deliberately. `paths.proposed` holds
# not-yet-applied change — sweeping it asks "does this other statement remain true?" about
# text that is not a statement about the system yet. And the generated `tasks.md` view
# would contribute every symbol its tasks name, forever. A check that cries wolf is one
# people learn to ignore, which costs more than the hits are worth.
PROPOSED_DIR="$(_cfg "from models.project import load; print(load().paths.get('proposed') or '')")"
_drop_staged() { if [ -n "$PROPOSED_DIR" ]; then grep -v "^$PROPOSED_DIR/" || true; else cat; fi; }

CHANGED=$(git diff --name-only "$BASE" "$HEAD_REF" -- "$DOC_SPEC" $AREA_SPECS 2>/dev/null | _drop_staged)
[ -z "$CHANGED" ] && { echo "no docs or source files changed between $BASE and $HEAD_REF"; exit 0; }

# Identifiers worth tracing: symbols, ADR section refs, and acceptance-criterion numbers.
# Taken from ADDED lines only — a removed mention is not a claim anyone can still read.
ADDED=$(git diff "$BASE" "$HEAD_REF" -- "$DOC_SPEC" $AREA_SPECS | grep '^+' | grep -v '^+++')
# The same exclusion for the ADDED side: an identifier introduced only by a staged file is
# not yet a claim anyone can read as true.

# Dunders and stdlib names match thousands of vendored files and bury the real hits.
# git grep (above) already excludes untracked .venv/node_modules; this drops the rest.
SYMS=$(printf '%s\n' "$ADDED" | grep -oE '\b[a-z_]+\.[a-z]{1,4}::[a-zA-Z_]+|\b_[a-z_]{4,}\b' \
        | sed 's/.*:://' | sort -u \
        | grep -vE '^_+$|^__|__$|^_(future|init|main|name|file|doc|all|version)' \
        | head -25)
# Two tiers, and the distinction is the whole point of the signal.
# EDITED: you changed the ADR file itself, so every other statement of it is suspect.
# CITED:  you merely referenced it as a reason; that is usually fine, so it is a count only.
ADRS_EDITED=$(printf '%s\n' "$CHANGED" | grep -oE "$(basename "${ADRS_DIR:-decisions}")/([0-9]{4})" | sed "s|.*/|record-|" | sort -u)
ADRS_CITED=$(printf '%s\n' "$ADDED" | grep -oE 'ADR-[0-9]{4}' | sort -u \
             | grep -vxF -f <(printf '%s\n' "$ADRS_EDITED" | grep . || echo __none__) || true)

echo "=== doc-drift sweep: $BASE..$HEAD_REF ==="
echo
echo "Files changed:"; printf '%s\n' "$CHANGED" | sed 's/^/  /'
echo

hits=0
if [ -n "$ADRS_CITED" ]; then
  n=$(printf '%s\n' "$ADRS_CITED" | grep -c .)
  echo "--- $n ADR(s) merely CITED as a reason (not edited): $(printf '%s ' $ADRS_CITED)"
  echo "    Not swept — citing an ADR does not change it. Sweeping these is where the noise lives."
  echo
fi

if [ -n "$ADRS_EDITED" ]; then
  echo "--- you EDITED these ADRs. Every OTHER file stating them is suspect ---"
  for a in $ADRS_EDITED; do
    others=$(git grep -l "$a" -- "$DOCS_DIR" $AREA_SPECS 2>/dev/null | grep -vxF -f <(printf '%s\n' "$CHANGED") || true)
    if [ -n "$others" ]; then
      echo "  $a is also stated in:"; printf '%s\n' "$others" | sed 's/^/      /'
      hits=$((hits+1))
    fi
  done
  echo
fi

if [ -n "$SYMS" ]; then
  echo "--- every OTHER file naming a symbol this change touched ---"
  for s in $SYMS; do
    others=$(git grep -l "\b$s\b" -- "$DOCS_DIR" $AREA_SPECS 2>/dev/null | grep -vxF -f <(printf '%s\n' "$CHANGED") || true)
    if [ -n "$others" ]; then
      n=$(printf '%s\n' "$others" | grep -c .)
      if [ "$n" -gt 12 ]; then
        echo "  $s: $n files — too broad to be a useful signal, not listed"
      else
        echo "  $s:"; printf '%s\n' "$others" | sed 's/^/      /'
        hits=$((hits+1))
      fi
    fi
  done
  echo
fi

echo "=== $hits identifier(s) are stated somewhere this change did NOT touch ==="
cat <<'NOTE'

READ THE HITS. For each one ask the only question that matters:
  "Does that other statement remain TRUE after my change?"

A hit is not a defect — most will be fine. But a hit you did not read is exactly how
a feature doc's criterion came to assert the opposite of the decision record it cites,
no task owned that file.
NOTE
exit 0
