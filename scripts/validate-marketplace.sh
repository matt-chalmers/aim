#!/usr/bin/env bash
# Verify that .claude-plugin/marketplace.json agrees with what is on disk.
#
#   scripts/validate-marketplace.sh
#
# WHY THIS EXISTS. No plugin's own test suite can see this failure: every plugin can be
# green while the marketplace is unusable, because what breaks is the INDEX, not the
# indexed. A `source` naming a directory that was moved, a plugin whose manifest name
# drifted from its entry, or a plugin added to the tree and never registered — each ships
# a marketplace that resolves nothing, and each is invisible from inside the plugin.
#
# Checked in both directions:
#   manifest -> disk   every entry's source exists, has a plugin.json, names match
#   disk -> manifest   every plugins/<collection>/<name>/ is registered
#
# The reverse direction is the one that catches the common mistake: writing a plugin and
# forgetting the entry, which looks like nothing at all until someone tries to install it.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 - "$@" <<'PY'
import json, sys
from pathlib import Path

root = Path.cwd()
manifest = root / ".claude-plugin" / "marketplace.json"
fail = []

if not manifest.exists():
    sys.exit(f"FAIL: no marketplace manifest at {manifest.relative_to(root)}")
try:
    doc = json.loads(manifest.read_text())
except json.JSONDecodeError as exc:
    sys.exit(f"FAIL: {manifest.relative_to(root)} is not valid JSON: {exc}")

for key in ("name", "owner", "plugins"):
    if key not in doc:
        fail.append(f"manifest is missing required key {key!r}")

entries = doc.get("plugins") or []
if not entries:
    fail.append("manifest declares no plugins — nothing would be installable")

# --- manifest -> disk ---------------------------------------------------------
seen, registered = {}, set()
for e in entries:
    name, src = e.get("name"), e.get("source")
    if not name or not src:
        fail.append(f"entry {e!r} is missing name or source")
        continue
    if name in seen:
        fail.append(f"duplicate plugin name {name!r}; a name identifies one plugin")
    seen[name] = src

    if not isinstance(src, str) or not src.startswith("./"):
        fail.append(f"{name}: source {src!r} must be a relative path starting './'")
        continue
    d = (root / src).resolve()
    if not d.is_dir():
        fail.append(f"{name}: source {src!r} does not exist")
        continue
    registered.add(d)

    pj = d / ".claude-plugin" / "plugin.json"
    if not pj.exists():
        fail.append(f"{name}: {src}/.claude-plugin/plugin.json is missing")
        continue
    try:
        got = json.loads(pj.read_text()).get("name")
    except json.JSONDecodeError as exc:
        fail.append(f"{name}: its plugin.json is not valid JSON: {exc}")
        continue
    if got != name:
        fail.append(
            f"{name}: entry says {name!r} but {src}/.claude-plugin/plugin.json "
            f"declares {got!r}; install resolves by the entry, so these must agree"
        )
    if (d / ".claude-plugin" / "marketplace.json").exists():
        fail.append(f"{name}: carries its own marketplace.json — a plugin is not a marketplace")

# --- disk -> manifest ---------------------------------------------------------
plugins_dir = root / "plugins"
found = 0
if plugins_dir.is_dir():
    for collection in sorted(p for p in plugins_dir.iterdir() if p.is_dir()):
        for cand in sorted(p for p in collection.iterdir() if p.is_dir()):
            if not (cand / ".claude-plugin" / "plugin.json").exists():
                continue
            found += 1
            if cand.resolve() not in registered:
                rel = cand.relative_to(root)
                fail.append(f"{rel} has a plugin.json but no entry in the manifest")

# An empty scan reads as clean, which is the failure this line prevents.
if found == 0:
    fail.append("no plugin directories found under plugins/ — wrong layout, or nothing shipped")

if fail:
    print("FAIL — the manifest and the tree disagree:", file=sys.stderr)
    for f in fail:
        print(f"  - {f}", file=sys.stderr)
    sys.exit(1)

print(f"OK — {len(seen)} plugin(s) registered and present: {', '.join(sorted(seen))}")
PY
