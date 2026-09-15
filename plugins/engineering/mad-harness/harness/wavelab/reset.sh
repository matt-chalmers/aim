#!/usr/bin/env bash
# Build or rebuild the two wavelab repositories from one base.
#
#   harness/wavelab/reset.sh [--root DIR]
#
# Two repositories, byte-identical except for one config block: the tracker backend. That
# is the whole design — a difference in what a wave produces is then attributable to the
# backend and nothing else.
#
# DESTRUCTIVE, and guarded accordingly: it will only remove a directory that carries the
# marker file it wrote itself. Pointing --root at something else fails rather than
# deleting it.
set -euo pipefail


ROOT="${WAVELAB_ROOT:-$HOME/harness-wavelab}"
while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOT="${2:?--root needs a directory}"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

HERE="$(cd "$(dirname "$0")" && pwd)"
PLUGIN="$(cd "$HERE/../.." && pwd)"
MARKER=".wavelab-generated"

case "$PLUGIN" in
  "$ROOT"|"$ROOT"/*) echo "refusing: --root is inside the plugin itself" >&2; exit 2 ;;
esac

command -v uv >/dev/null || { echo "uv is required" >&2; exit 2; }

# THE FIRST THING, because everything after it is meaningless otherwise: a dispatched
# agent resolves against the INSTALLED plugin, not this checkout.
"$HERE/check-plugin-fresh.sh" || {
  echo "refusing to build a wavelab that would test a stale plugin" >&2; exit 4; }
command -v bd >/dev/null || echo "WARNING: bd is not on PATH — the beads repo will not seed" >&2

seed_repo() {  # $1 = name, $2 = tracker yaml block
  # Separate declarations: in one `local`, a later assignment cannot reference an
  # earlier one on the same line, and `set -u` turns that into an unbound-variable
  # abort rather than an empty path.
  local name="$1"
  local block="$2"
  local dir="$ROOT/$name"

  if [ -d "$dir" ] && [ ! -f "$dir/$MARKER" ]; then
    echo "refusing to remove $dir — no $MARKER, so this was not ours" >&2
    exit 3
  fi
  rm -rf "$dir"
  mkdir -p "$dir"
  cp -R "$HERE/base/." "$dir/"
  date -u +"generated %Y-%m-%dT%H:%M:%SZ by harness/wavelab/reset.sh" > "$dir/$MARKER"

  printf '%s\n' "$block" > "$dir/harness.yaml"

  ( cd "$dir"
    uv lock --quiet            # python-uv detects the LOCKFILE, not the manifest
    uv sync --quiet
    git init -q .
    git add -A
    git -c user.email=wavelab@example.com -c user.name=wavelab \
        commit -q -m "base: a green suite, before any wave"
  )
  echo "  $name  $dir"
}

COMMON='name: Wavelab
slug: wavelab
areas:
  - path: src
    label: library code
  - path: tests
    label: tests
paths:
  docs: docs
  proposed: docs/proposed
lanes:
  backend:
    stacks: [python-uv]
    agent: fullstack-engineer
    cap: 2
stacks:
  - python-uv
testing:
  aggregate_commands:
    "Whole repo": "`uv run pytest`"
security:
  paths: []
  tokens: []
  invariants:
    - Never widen a public function signature without updating every caller.
signals:
  megafile_lines: 1000'

echo "wavelab repositories:"
seed_repo beads   "$COMMON
beads:
  prefix: WL
swarm:
  merge_slot: WL-merge-slot
tracker:
  backend: beads
  limits:
    record_bytes: 64000"

seed_repo mdfiles "$COMMON
beads:
  prefix: WL
swarm:
  merge_slot: WL-merge-slot
tracker:
  backend: mdfiles
  dir: .harness/tasks
  export: docs/tasks
  limits:
    record_bytes: null"

# The tracker is seeded THROUGH THE SHIM, for both repos. That is deliberate: the seeding
# itself is the first end-to-end exercise of the abstraction, and using `bd` for one and
# markdown for the other would leave the two repos' task text subtly different.
"$HERE/seed-epic.sh" --root "$ROOT" beads
"$HERE/seed-epic.sh" --root "$ROOT" mdfiles

echo
echo "Both repos are at an identical green base with the same epic."
echo "Next:  harness/wavelab/dispatch-wave.sh beads"
