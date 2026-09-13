#!/usr/bin/env bash
# Is the INSTALLED plugin the code in this working tree?
#
#   harness/wavelab/check-plugin-fresh.sh
#
# WHY THIS EXISTS, AND IT NEARLY COST A WHOLE WAVE. A dispatched agent is resolved by name
# against the INSTALLED plugin, not against this checkout — so a wave dispatches whatever
# was cached at install time. The cache here was five days and twenty-five commits stale,
# predating the entire tracker port, and a live wave would have exercised the OLD prompts
# while looking like a valid test of the new ones.
#
# `claude plugin update` does not help on its own: for a directory-sourced plugin it
# compares VERSION STRINGS, not content, and reports "already at the latest version" while
# the cache and the tree differ by any number of commits. Bump `version` in
# .claude-plugin/plugin.json, then update.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PLUGIN="$(cd "$HERE/../.." && pwd)"
NAME=$(python3 -c "import json;print(json.load(open('$PLUGIN/.claude-plugin/plugin.json'))['name'])")
VERSION=$(python3 -c "import json;print(json.load(open('$PLUGIN/.claude-plugin/plugin.json'))['version'])")
CACHE="$HOME/.claude/plugins/cache/$NAME/$NAME/$VERSION"

if [ ! -d "$CACHE" ]; then
  echo "STALE: no installed copy at version $VERSION." >&2
  echo "  The dispatched agents would run whatever WAS installed, not this tree." >&2
  echo "  Fix: bump version in .claude-plugin/plugin.json, then" >&2
  echo "       claude plugin update $NAME@$NAME" >&2
  exit 1
fi

DRIFT=0
while IFS= read -r rel; do
  if ! diff -q "$PLUGIN/$rel" "$CACHE/$rel" >/dev/null 2>&1; then
    echo "  drifted: $rel"
    DRIFT=$((DRIFT+1))
  fi
done < <(cd "$PLUGIN" && git ls-files agents commands skills harness/tracker)

if [ "$DRIFT" -gt 0 ]; then
  echo "STALE: $DRIFT file(s) differ between this tree and the installed plugin." >&2
  echo "  Bump version in .claude-plugin/plugin.json, then: claude plugin update $NAME@$NAME" >&2
  exit 1
fi
echo "OK — installed plugin $NAME@$VERSION matches this working tree."
