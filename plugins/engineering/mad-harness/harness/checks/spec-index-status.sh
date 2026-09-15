#!/usr/bin/env bash
# Answer campaign-loop §3a's reuse / delta / rebuild decision mechanically.
#
# The spec index records the SHA it was generated against and every doc it cites. Whether it is
# still good is therefore a diff, not a judgement: if nothing it cites has moved since that SHA,
# reuse it; if some have, delta-survey those; if there is no index, survey fully.
#
# Usage: harness/checks/spec-index-status.sh <epic-id>
set -u

# Record where we were invoked from. This script reaches into the harness via a
# subshell or a computed path, so Python still runs with the harness as its cwd —
# and the harness carries its own harness.yaml, which the resolver would read as
# the project. Exported here so every subshell inherits it.
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
# Operate on the repository being WORKED ON, not the one the harness lives in. As an
# installed plugin those are different directories, and cd-ing to the harness's own
# root made every one of these checks read the wrong tree — silently, because an empty
# result is indistinguishable from a clean one.
PWD_REPO="${MAD_HARNESS_REPO:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
export PWD_REPO
cd "$PWD_REPO"
# The staging directory is a project fact — read it from config rather than assuming
# one layout. A hard-coded glob finds nothing in a repo that names it differently, and
# a check that finds nothing reports CLEAN.
# MAD_HARNESS_REPO must cross into the subshell: it cd's into the harness to reach
# the module path, which would otherwise resolve the config of the PLUGIN rather
# than of the repository being checked.
HARNESS_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")/.." && pwd)"
PROPOSED="$(cd "$HARNESS_DIR" && MAD_HARNESS_REPO="$PWD_REPO" env -u VIRTUAL_ENV uv run python -c \
  "from models.project import load; print(load().paths.get('proposed',''))" 2>/dev/null)"
export PROPOSED
# ONE INTERPRETER. The body needs PyYAML for the index's frontmatter, and the lookup above
# just ran under the harness venv that has it — then this line exec'd the SYSTEM python3,
# which on macOS does not, and every epic answered "PyYAML required". `--project` runs the
# harness's environment WITHOUT changing the working directory, which the glob and the
# `git diff` below need to be the repository being checked.
exec env -u VIRTUAL_ENV uv run --project "$HARNESS_DIR" python - "${1:?usage: spec-index-status.sh <epic-id>}" <<'PY'
import sys, glob, os, re, subprocess


def _proposed():
    """The staging directory, from config via the wrapper's PROPOSED export.

    No default: a wrong guess globs a path that does not exist, finds nothing, and
    reports clean — the failure this check exists to catch, turned on itself.
    """
    d = os.environ.get("PROPOSED", "").strip()
    if not d:
        sys.exit("harness.yaml declares no paths.proposed — cannot locate staged files")
    return d
try: import yaml
except ImportError: sys.exit("PyYAML missing from the harness venv — run `uv sync` in harness/")
epic = sys.argv[1]
hits = glob.glob(f"{_proposed()}/{epic}*/spec-index.md")
if not hits:
    print(f"REBUILD — no spec index for {epic}. Dispatch a full SURVEY."); sys.exit(0)
p = hits[0]
m = re.match(r'^---\n(.*?)\n---\n', open(p, encoding='utf-8').read(), re.S)
if not m: sys.exit(f"✗ {p} has no frontmatter — the citations and baseline live there")
fm = yaml.safe_load(m.group(1)) or {}
sha, cites = fm.get("generated_sha"), (fm.get("cites") or [])
if not sha or not cites: sys.exit(f"✗ {p} must record generated_sha and a non-empty cites list")
missing = [c for c in cites if not os.path.exists(c)]
try:
    out = subprocess.run(["git","diff","--name-only",f"{sha}..HEAD","--"]+cites,
                         capture_output=True, text=True, timeout=60)
    if out.returncode: sys.exit(f"✗ git diff failed against {sha} — is that SHA in this history?\n{out.stderr.strip()}")
    moved = [x for x in out.stdout.split() if x]
except Exception as e: sys.exit(f"✗ {e}")
print(f"index: {p}\nbaseline: {sha}  ·  cites {len(cites)} docs  ·  verdict recorded: {fm.get('verdict','?')}")
if missing: print("  ! cited doc no longer exists: " + ", ".join(missing))
if moved or missing:
    print("\nDELTA — hand analyst-survey this index plus the changed paths, and ask it to verify and extend:")
    for x in sorted(set(moved)|set(missing)): print("  " + x)
else:
    print("\nREUSE — nothing it cites has moved. Do not re-dispatch; say so in the report with the index date.")
print("\nNote: the VERDICT is stale regardless if the epic's children changed — adequacy is judged\nagainst what is being built, not only against the corpus.")
PY
