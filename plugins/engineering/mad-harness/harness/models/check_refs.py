"""Every plugin path a skill, command or agent names must exist.

The prose points at scripts by absolute plugin path — `${CLAUDE_PLUGIN_ROOT}/harness/checks/...`
— and nothing between writing that line and an orchestrator running it checks that the
file is there. `campaign-loop` named `check-task-size.sh` at three sites for a script
that shipped as `check-record-size.sh`; the §0 pre-flight errored on every run, one line
below a paragraph explaining how a previous version of that same step silently did not
run. A rename is the most ordinary edit there is, and this is the check that makes one
loud before it ships.

AND IT MUST EXIST FOR THE CONSUMER, not merely on the author's disk. Presence was checked
with `Path.exists()`, but the plugin is distributed as a clone of this repository — the
marketplace names `./plugins/engineering/mad-harness` as its source — so what ships is what
git tracks. Those are different sets: `proposals/`, `.claude/` and `independence_check.md`
are all present here and ignored, so a reference to one passed this check locally and would
have been a dead path in every installation. A check that validates a different set from the
one it ships is the shape this corpus treats as worse than no check.

Validates PLUGIN files, so it resolves from the harness's own location rather than the
consuming repository — the same orientation as `check-analyst-mirror.sh`.
"""

from __future__ import annotations

import re
import subprocess
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


def tracked(plugin: Path = PLUGIN) -> set[str] | None:
    """Every path git tracks under the plugin, plus every directory on the way to one.

    `None` when this is not a checkout — an installed plugin cache may not be one, and
    reporting every reference as unshipped there would be the confident-wrong answer. The
    caller then falls back to presence alone and says so, the same posture
    `check-docs.sh` takes when `d2` is absent.
    """
    try:
        out = subprocess.run(
            ("git", "-C", str(plugin), "ls-files", "-z"),
            capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    files = {p for p in out.split("\0") if p}
    dirs = {"/".join(p.split("/")[:i]) for p in files for i in range(1, p.count("/") + 1)}
    return files | dirs


def missing(plugin: Path = PLUGIN) -> dict[str, list[str]]:
    """Referenced and not there at all."""
    return {rel: at for rel, at in references(plugin).items() if not (plugin / rel).exists()}


def unshipped(plugin: Path = PLUGIN) -> dict[str, list[str]]:
    """Referenced, present on this disk, and NOT tracked — so absent for every consumer.

    Kept separate from `missing` because the fix differs: a missing path is restored or the
    prose corrected, where an unshipped one is committed or stopped being referenced.
    """
    known = tracked(plugin)
    if known is None:
        return {}
    return {
        rel: at
        for rel, at in references(plugin).items()
        if (plugin / rel).exists() and rel.rstrip("/") not in known
    }


def _report(label: str, bad: dict[str, list[str]]) -> None:
    print(f"\nFAIL: {len(bad)} referenced path(s) {label}:", file=sys.stderr)
    for rel, at in sorted(bad.items()):
        print(f"  - {rel}", file=sys.stderr)
        for site in at:
            print(f"      {site}", file=sys.stderr)


def main() -> int:
    # These call the predicates rather than restating them. `main` used to keep its own copy
    # of the presence test, so a change to `missing` would not have reached `make refs` —
    # one fact, two statements, which is the defect this repository ranks highest.
    refs = references()
    gone, local_only = missing(), unshipped()
    known = tracked()
    print(f"plugin refs: {len(refs)} distinct paths named across {', '.join(PROSE_DIRS)}")
    if known is None:
        print("         not a checkout — presence checked, shipping not verified")
    if not gone and not local_only:
        print(f"OK — every referenced plugin path exists{'' if known is None else ' and ships'}.")
        return 0
    if gone:
        _report("do not exist", gone)
        print(
            "\nA renamed or removed script is still named by the prose above. Either restore "
            "the file or fix every site.",
            file=sys.stderr,
        )
    if local_only:
        _report("exist here but are NOT TRACKED, so they ship to nobody", local_only)
        print(
            "\nThe plugin is distributed as a clone of this repository, so an ignored or "
            "untracked path is absent in every installation however present it is here. "
            "Either commit it or stop referencing it from prose an agent executes.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
