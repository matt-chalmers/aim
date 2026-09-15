#!/usr/bin/env bash
# Seed one wavelab repo with the epic and its three tasks, THROUGH THE SHIM.
#
#   harness/wavelab/seed-epic.sh [--root DIR] <beads|mdfiles>
#
# Through the shim on purpose. Seeding with `bd` for one repo and markdown for the other
# would leave their task text subtly different, and a differential test whose inputs
# differ proves nothing. It is also the first end-to-end exercise of the abstraction.
set -euo pipefail


ROOT="${WAVELAB_ROOT:-$HOME/harness-wavelab}"
while [ "${1:-}" = "--root" ]; do ROOT="${2:?}"; shift 2; done
NAME="${1:?usage: seed-epic.sh [--root DIR] <beads|mdfiles>}"
REPO="$ROOT/$NAME"
[ -d "$REPO" ] || { echo "no such repo: $REPO — run reset.sh first" >&2; exit 2; }

HERE="$(cd "$(dirname "$0")" && pwd)"
TK="$(cd "$HERE/.." && pwd)/tracker/tk.sh"

# THE LAB POINTS AT ITS TARGET BY STANDING IN IT. It used to export MAD_HARNESS_REPO,
# which every wrapper — and every agent dispatch.py spawned, since it inherits the
# environment — then honoured ahead of resolving anything. So the one question a real
# project needs answered ("which repository?") was answered for it, and a resolver that
# returned the plugin's own directory passed every wave here while a real project got
# an empty backlog with exit 0. Nothing in this lab sets MAD_HARNESS_REPO or
# MAD_HARNESS_CALLER_PWD: the wrappers record the caller's directory themselves.
tk() { ( cd "$REPO" && "$TK" "$@" ); }

if [ "$NAME" = "beads" ]; then
  ( cd "$REPO" && bd init >/dev/null 2>&1 ) || {
    echo "bd init failed in $REPO" >&2; exit 3; }
fi

EPIC=$(tk create "Normalise contact details before assembly" -t epic -p 1 \
  --description "clean_contact currently passes its input through. Give it real
normalisers, one per field, and wire them in.

ACCEPTANCE
- normalise_email and normalise_phone each live in their own module, with tests.
- clean_contact uses both, and its existing test still passes.
- No public signature changes.")

A=$(tk create "Add normalise_email" --parent "$EPIC" -p 1 --description \
"Add \`normalise_email(value: str) -> str\` in src/wavelab/email.py.

ACCEPTANCE
- Strips surrounding whitespace and lowercases the address.
- Returns '' for None or a blank string rather than raising.
- Tests in tests/test_email.py cover the happy path, blank, None and mixed case.
- SURFACE: none — a new module, no existing caller.")

B=$(tk create "Add normalise_phone" --parent "$EPIC" -p 1 --description \
"Add \`normalise_phone(value: str) -> str\` in src/wavelab/phone.py.

ACCEPTANCE
- Removes spaces, hyphens and brackets. A '+' survives only when it is the first
  character of the number once brackets and whitespace are gone; any other '+' is
  removed. Worked: '(+61) 400 000 000' -> '+61400000000', '00 61 400' -> '0061400'.
- Returns '' for None or a blank string rather than raising.
- Tests in tests/test_phone.py cover the happy path, blank, None and punctuation.
- SURFACE: none — a new module, no existing caller.")

C=$(tk create "Wire both normalisers into clean_contact" --parent "$EPIC" -p 2 --description \
"Use normalise_email and normalise_phone in \`clean_contact\`.

ACCEPTANCE
- clean_contact normalises the 'email' and 'phone' keys when present, leaving others alone.
- Absent keys must not be invented.
- tests/test_contact.py still passes unchanged, and gains coverage for the new behaviour.
- SURFACE: clean_contact is the one public entry point; its signature must not change.")

tk dep "$C" "$A"
tk dep "$C" "$B"

mkdir -p "$REPO/docs/proposed/$EPIC-normalise"
( cd "$REPO" && "$(cd "$HERE/.." && pwd)/tracker/render-epic.sh" \
  "$EPIC" --write "$REPO/docs/proposed/$EPIC-normalise/tasks.md" )

( cd "$REPO"
  git add -A
  git -c user.email=wavelab@example.com -c user.name=wavelab \
      commit -q -m "seed: the normalise-contact epic" )

echo "  seeded $NAME: epic $EPIC — wave 1 = $A, $B (parallel) · wave 2 = $C"
