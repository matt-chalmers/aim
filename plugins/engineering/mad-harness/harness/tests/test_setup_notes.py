"""The upgrade-notes reader: what is already marked, and nothing it would have to invent."""

from __future__ import annotations

import json
import re
import subprocess

from models.project import UPGRADE_NOTES, plugin_version
from models.resolve import PLUGIN_ROOT
from models.setup_notes import sections, sections_after

FIXTURE = """# Upgrading

Preamble mentioning **mechanical** and **ask the owner** in prose, which is not an item.

## Notes per version

### 0.9.0

Intro prose.

- **ask the owner** — declare `ports:`, every TCP port the servers bind, by
  name. A continuation line joins its item.
- **mechanical** — rename `tasks:` to `beads:`.
- An untagged bullet explaining the release.

### 0.10.0

- **mechanical** — re-stamp `harness.version`.

### 0.9.10

- **mechanical** — nothing.

## Something after the notes

- **mechanical** — not a note: outside the section.
"""


def test_sections_are_read_oldest_first_with_items_split_by_tag():
    got = sections(FIXTURE)
    assert [s.version for s in got] == ["0.9.0", "0.9.10", "0.10.0"], (
        "numeric order, not text"
    )
    first = got[0]
    assert first.ask == [
        "declare `ports:`, every TCP port the servers bind, by name. A continuation line joins its item."
    ]
    assert first.mechanical == ["rename `tasks:` to `beads:`."]
    assert first.other == 1


def test_only_sections_newer_than_the_stamp_and_unstamped_gets_all():
    assert [s.version for s in sections_after("0.9.0", FIXTURE)] == ["0.9.10", "0.10.0"]
    assert [s.version for s in sections_after("0.10.0", FIXTURE)] == []
    assert len(sections_after(None, FIXTURE)) == 3


def test_nothing_outside_the_notes_section_is_an_item():
    assert sum(len(s.mechanical) for s in sections(FIXTURE)) == 3


def test_the_real_notes_parse_completely():
    """Every version heading is a section, and every tagged item is counted — compared with a
    plain grep of the same tags, so a tag the reader misses is a failure here."""
    text = UPGRADE_NOTES.read_text()
    got = sections(text)
    assert len(got) == len(re.findall(r"^### \d", text, re.M))
    assert sum(len(s.mechanical) for s in got) == len(
        re.findall(r"^- \*\*mechanical\*\* — ", text, re.M)
    )
    assert sum(len(s.ask) for s in got) == len(
        re.findall(r"^- \*\*ask the owner\*\* — ", text, re.M)
    )
    assert got[-1].version == plugin_version()


def test_the_wrapper_emits_json():
    out = subprocess.run(
        [
            str(PLUGIN_ROOT / "harness" / "setup" / "notes.sh"),
            "--after",
            "0.11.0",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    versions = [s["version"] for s in json.loads(out)]
    assert versions[0] == "0.12.0" and versions[-1] == plugin_version()
