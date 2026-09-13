#!/usr/bin/env bash
# Atomic fidelity check — the ONE command to run per screen/component, so the
# two mandatory steps (read handover source + visual side-by-side) can't be
# skipped independently: running this does both.
#
#   harness/verify/fidelity-check.sh <name> <HandoverComponent> <ourPath> [width]
#
# It (1) prints the handover SOURCE for the component (+ where to find its
# sub-components), and (2) generates the side-by-side composite at
# $SHOTS_DIR/cmp/<name>.png. Read the source, open the composite, fix, then
# re-run to CONFIRM before committing. A screen is not "done" without pasting
# BOTH the source excerpt and the (re-run) composite as evidence.
set -euo pipefail

# Record where we were invoked from. This script reaches into the harness via a
# subshell or a computed path, so Python still runs with the harness as its cwd —
# and the harness carries its own harness.yaml, which the resolver would read as
# the project. Exported here so every subshell inherits it.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"

name="${1:?usage: fidelity-check.sh <name> <HandoverComponent> <ourPath> [width]}"
comp="${2:?missing handover component}"
ourPath="${3:?missing our route path}"
width="${4:-1280}"

HANDOVER="${HANDOVER_DIR:?set HANDOVER_DIR to your design handover package}"

# Scratch output follows SCRATCHPAD/TMPDIR rather than assuming /tmp, which is one
# machine's layout and not writable everywhere.
SHOTS_DIR="${SHOTS_DIR:-${SCRATCHPAD:-${TMPDIR:-/tmp}}/shots}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "══════════════════════════════════════════════════════════════════════"
echo " HANDOVER SOURCE — $comp   (READ THIS BEFORE TOUCHING CODE)"
echo "══════════════════════════════════════════════════════════════════════"
src_file="$(grep -rl "function $comp" "$HANDOVER/src" 2>/dev/null | head -1 || true)"
if [[ -z "$src_file" ]]; then
  echo "  ⚠  No 'function $comp' found under $HANDOVER/src — check the name."
else
  echo "  file: ${src_file#$HANDOVER/}"
  echo "  ----------------------------------------------------------------"
  awk "/function $comp[ (]/,/^}/" "$src_file"
  echo "  ----------------------------------------------------------------"
  echo "  Sub-components referenced above (read these too):"
  awk "/function $comp[ (]/,/^}/" "$src_file" \
    | grep -oE "<[A-Z][A-Za-z]+" | tr -d '<' | sort -u | tr '\n' ' '
  echo ""
fi

echo "══════════════════════════════════════════════════════════════════════"
echo " SIDE-BY-SIDE COMPOSITE"
echo "══════════════════════════════════════════════════════════════════════"
node "$HERE/fidelity-compare.mjs" "$name" "$comp" "$ourPath" "$width"
echo ""
echo "NEXT: 1) read the source above  2) open $SHOTS_DIR/cmp/$name.png"
echo "      3) fix every delta        4) RE-RUN this command to confirm"
echo "A screen is NOT done until you've pasted the source + the re-run composite."
