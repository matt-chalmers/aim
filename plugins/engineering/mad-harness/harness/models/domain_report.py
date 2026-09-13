"""Report where the harness's prose uses a consuming project's domain vocabulary.

CLUES, NOT A VERDICT. Every line here is a candidate for a reader — human or agent — to
judge. That is the whole design, and it is a retreat from something that did not work.

The obvious design is a blocking test: derive the project's terms, fail if any appears
in a prompt. It was built that way and the numbers killed it.

    hand-curated 20 terms   ->  0 hits, but omitted `season`, which had leaked twice
    derived 133 terms       -> 94 hits, every one of them false
    derived minus 15 hand-excluded -> 125 terms, 0 hits

The false hits were `system`, `health`, `messages`, `analytics`, `proposals` — words a
product uses for its features and a harness uses for its own machinery. Suppressing them
took a hand-written exclusion list, which grows every time the product adds a
generic-sounding feature. So the maintenance never ends, and the failure mode is a CI
break on text that was always fine.

**A domain word in a harness prompt is not automatically wrong.** "Check the message
board" is a leak; "the orchestrator's session receives the message" is not. Only a reader
can tell, which makes this a thing to look at, not a thing to fail.

What IS a reliable gate, and stays one: the project's NAME, SLUG and BEAD PREFIX. Those
are unambiguous, and `test_no_project_identifier_leaks_into_the_shipped_corpus` blocks
on them.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from .project import load
from .resolve import HARNESS, PLUGIN_ROOT

TEXT = {".md", ".py", ".sh", ".mjs", ".yaml", ".yml", ".txt", ".ini"}
SKIP_PARTS = {".venv", "__pycache__", "node_modules"}


def harness_files() -> list[Path]:
    """The plugin's own prose — never the consuming repository's."""
    out: list[Path] = []
    for d in (
        PLUGIN_ROOT / "agents",
        PLUGIN_ROOT / "commands",
        PLUGIN_ROOT / "skills",
        HARNESS,
    ):
        if d.is_dir():
            out += [
                f
                for f in d.rglob("*")
                if f.suffix in TEXT
                and f.is_file()
                and not any(p in SKIP_PARTS for p in f.parts)
            ]
    return out + [f for f in PLUGIN_ROOT.glob("*.md")]


def main() -> int:
    nouns = load().domain_nouns()
    if not nouns:
        print("this project declares no domain vocabulary — nothing to report.")
        return 0

    files = harness_files()
    hits: dict[str, list[str]] = defaultdict(list)
    for path in sorted(files):
        rel = path.relative_to(PLUGIN_ROOT)
        # This module and its tests necessarily quote the vocabulary they discuss.
        if rel.name in {"domain_report.py", "test_modules.py"}:
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for n in nouns:
                if re.search(rf"\b{re.escape(n)}\w*\b", line, re.I):
                    hits[n].append(f"{rel}:{i}: {line.strip()[:88]}")
                    break

    print(f"domain vocabulary: {len(nouns)} terms, {len(files)} harness files scanned")
    if not hits:
        print("\nNo harness prose uses this project's domain vocabulary.")
        return 0

    total = sum(len(v) for v in hits.values())
    print(f"\n{total} lines worth READING — each is a clue, not a verdict:\n")
    for term, lines in sorted(hits.items(), key=lambda kv: -len(kv[1])):
        print(f"  {term} ({len(lines)})")
        for line in lines[:4]:
            print(f"      {line}")
        if len(lines) > 4:
            print(f"      … {len(lines) - 4} more")
    print(
        "\nAsk of each: would this sentence be WRONG in a project without that concept?\n"
        "If yes it is a leak. If the word is doing ordinary work, add it to\n"
        "`domain.ambiguous` so it stops appearing here."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
