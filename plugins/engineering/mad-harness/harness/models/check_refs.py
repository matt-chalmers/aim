"""Every plugin path a skill, command or agent names must exist.

The prose points at scripts by absolute plugin path — `${CLAUDE_PLUGIN_ROOT}/harness/checks/...`
— and nothing between writing that line and an orchestrator running it checks that the
file is there. `campaign-loop` named `check-task-size.sh` at three sites for a script
that shipped as `check-record-size.sh`; the §0 pre-flight errored on every run, one line
below a paragraph explaining how a previous version of that same step silently did not
run. A rename is the most ordinary edit there is, and this is the check that makes one
loud before it ships.

Validates PLUGIN files, so it resolves from the harness's own location rather than the
consuming repository — the same orientation as `check-analyst-mirror.sh`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from .resolve import HARNESS

PLUGIN = HARNESS.parent

#: The directories whose prose an agent or orchestrator executes verbatim.
PROSE_DIRS = ("skills", "commands", "agents", "hooks")

#: `${CLAUDE_PLUGIN_ROOT}/x/y` and `$CLAUDE_PLUGIN_ROOT/x/y`. The path class stops at
#: whitespace, quotes and backticks; trailing sentence punctuation is stripped after.
_REF = re.compile(r"\$\{?CLAUDE_PLUGIN_ROOT\}?/([A-Za-z0-9_./-]+)")


def references(plugin: Path = PLUGIN) -> dict[str, list[str]]:
    """Every referenced relative path -> the `file:line` sites that name it."""
    sites: dict[str, list[str]] = {}
    for d in PROSE_DIRS:
        root = plugin / d
        if not root.is_dir():
            continue
        for f in sorted(root.rglob("*.md")):
            for n, line in enumerate(f.read_text().splitlines(), 1):
                for m in _REF.finditer(line):
                    rel = m.group(1).rstrip(".,;:)")
                    sites.setdefault(rel, []).append(f"{f.relative_to(plugin)}:{n}")
    return sites


def missing(plugin: Path = PLUGIN) -> dict[str, list[str]]:
    return {rel: at for rel, at in references(plugin).items() if not (plugin / rel).exists()}


def main() -> int:
    refs = references()
    gone = {rel: at for rel, at in refs.items() if not (PLUGIN / rel).exists()}
    print(f"plugin refs: {len(refs)} distinct paths named across {', '.join(PROSE_DIRS)}")
    if not gone:
        print("OK — every referenced plugin path exists.")
        return 0
    print(f"\nFAIL: {len(gone)} referenced path(s) do not exist:", file=sys.stderr)
    for rel, at in sorted(gone.items()):
        print(f"  - {rel}", file=sys.stderr)
        for site in at:
            print(f"      {site}", file=sys.stderr)
    print(
        "\nA renamed or removed script is still named by the prose above. Either restore "
        "the file or fix every site.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
