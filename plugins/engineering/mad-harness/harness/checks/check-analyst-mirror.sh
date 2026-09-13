#!/usr/bin/env bash
# The analyst is split in two so the SURVEY can run at lower effort than the AUDIT:
#
#   agents/analyst-survey.md            tier: worker     — breadth-first corpus sweep
#   agents/analyst.md                   tier: strong     — judges a drafted plan
#
# The concrete model and effort now come from the tier (harness/models/tiers.yaml);
# this script asserts only that the two DIFFER, which is what the split buys.
#
# Effort is one value per agent file and the Agent tool has no per-dispatch override, so the
# split is the only way to tier them. The cost of splitting is DRIFT — the exact failure the
# skill/command factoring exists to prevent — and the two files share a block of GUARDRAILS
# ("you never write requirements, and you never enhance them"), which must be present
# unconditionally rather than behind a skill load that might not happen.
#
# So the shared block is duplicated deliberately, and this script makes divergence LOUD.
# Run it after touching either file. Exit 0 = identical.
set -euo pipefail

# Record where we were invoked from. This script reaches into the harness via a
# subshell or a computed path, so Python still runs with the harness as its cwd —
# and the harness carries its own harness.yaml, which the resolver would read as
# the project. Exported here so every subshell inherits it.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"

# THIS CHECK VALIDATES PLUGIN FILES, so it resolves from the script's own location —
# the opposite correction from its siblings, which were fixed to read the CONSUMING
# repo. The agents it compares ship with the harness; a consuming repo has no copy.
PLUGIN="$(cd "$(dirname "$0")/../.." && pwd)"
A="$PLUGIN/agents/analyst.md"
B="$PLUGIN/agents/analyst-survey.md"

extract() {  # print every MIRRORED BLOCK body in $1
  awk '/BEGIN MIRRORED BLOCK -->/{f=1;next} /<!-- END MIRRORED BLOCK -->/{f=0} f' "$1"
}

for f in "$A" "$B"; do
  [ -f "$f" ] || { echo "MISSING: $f" >&2; exit 2; }
  n=$(grep -c 'BEGIN MIRRORED BLOCK' "$f" || true)
  [ "$n" -eq 2 ] || { echo "FAIL: $(basename "$f") has $n mirrored blocks, expected 2" >&2; exit 3; }
done

if diff -u <(extract "$A") <(extract "$B") > ${TMPDIR:-/tmp}/analyst-mirror-$$.diff; then
  echo "OK — analyst.md and analyst-survey.md mirrored blocks are identical ($(extract "$A" | wc -l | tr -d ' ') lines)."
else
  echo "DRIFT: the shared guardrail blocks have diverged." >&2
  echo "  This is the failure the split was known to risk. Reconcile before dispatching either agent." >&2
  cat ${TMPDIR:-/tmp}/analyst-mirror-$$.diff >&2
  exit 1
fi

# The whole point of the split: the two efforts must actually differ.
ea=$(sed -n 's/^effort: *//p' "$A" | head -1)
eb=$(sed -n 's/^effort: *//p' "$B" | head -1)
echo "effort — analyst(AUDIT)=$ea  analyst-survey(SURVEY)=$eb"
[ "$ea" = "xhigh" ] || { echo "FAIL: the AUDIT must stay xhigh — it has caught a false premise in every plan it has read." >&2; exit 4; }
[ "$eb" = "$ea" ] && { echo "FAIL: efforts are equal, so the split buys nothing." >&2; exit 5; }
echo "OK — tiering is in effect."
