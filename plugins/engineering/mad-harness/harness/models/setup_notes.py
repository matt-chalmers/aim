"""`docs/upgrading.md`, read by what it ALREADY marks — so the upgrade opening gets ~40 lines
of JSON in place of 400 lines of prose.

NO RETROFIT. The notes run to dozens of version sections and nothing read them but an agent.
Adding machine-readable tags across every historical section would be a field nothing keeps
honest — the lesson `frameworks/_template.yaml` records about an inert `detect` key. Two
markers are already there and already enforced: a `### <version>` heading per release
(`test_upgrade.py::test_every_version_has_upgrade_notes`) and the `**mechanical**` /
`**ask the owner**` item tags the page's own preamble defines. This reads exactly those.

WHAT IT DOES NOT DO: say which setup block an item touches. That is a reading of the item's
prose, which stays with setup's agent (spec D-18) — the notes carry no block field and none
is to be added.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field

from .project import UPGRADE_NOTES, _version_key

HEADING = re.compile(r"^### (\d+(?:\.\d+)*)\s*$")
ITEM = re.compile(r"^- \*\*(mechanical|ask the owner)\*\* — (.*)$")
NOTES_START = "## Notes per version"


@dataclass
class Section:
    version: str
    mechanical: list[str] = field(default_factory=list)
    ask: list[str] = field(default_factory=list)
    #: Untagged bullets — prose explaining the release. Counted, so a reader sees how much
    #: was not handed to it, never silently dropped.
    other: int = 0


def sections(text: str | None = None) -> list[Section]:
    """Every version section, oldest first, items split by tag."""
    text = UPGRADE_NOTES.read_text() if text is None else text
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == NOTES_START)
    except StopIteration:
        return []
    out: list[Section] = []
    current: Section | None = None
    item: list[str] | None = None  # the open item's bucket, so a continuation joins it
    for ln in lines[start + 1 :]:
        if ln.startswith("## "):
            break
        m = HEADING.match(ln)
        if m:
            current = Section(m.group(1))
            out.append(current)
            item = None
            continue
        if current is None:
            continue
        m = ITEM.match(ln)
        if m:
            bucket = current.mechanical if m.group(1) == "mechanical" else current.ask
            bucket.append(m.group(2).strip())
            item = bucket
            continue
        if ln.startswith("- "):
            current.other += 1
            item = None
            continue
        if item is not None and ln.startswith("  ") and ln.strip():
            item[-1] = f"{item[-1]} {ln.strip()}"
            continue
        if not ln.strip():
            item = None
    out.sort(key=lambda s: _version_key(s.version))
    return out


def sections_after(stamp: str | None, text: str | None = None) -> list[Section]:
    """The sections newer than `stamp`, oldest first. No stamp — a config older than the
    stamp itself — gets every section."""
    every = sections(text)
    if stamp is None:
        return every
    floor = _version_key(stamp)
    return [s for s in every if _version_key(s.version) > floor]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    stamp = None
    if "--after" in args:
        stamp = args[args.index("--after") + 1]
    found = sections_after(stamp)
    if "--json" in args:
        print(json.dumps([asdict(s) for s in found], indent=2))
        return 0
    for s in found:
        print(
            f"{s.version}: {len(s.mechanical)} mechanical, {len(s.ask)} ask the owner, {s.other} other"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
