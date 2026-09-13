"""Retire an epic's staging folder by MOVING it, rather than deleting it.

WHY NOT JUST DELETE, which is what fold-in ② does today. The content is recoverable —
the staging folder is git-tracked, so `git log --diff-filter=D` finds every retired one —
but nothing records WHICH epic put a given criterion into a feature doc, so the derivation
is archaeology nobody knows to perform.

THE ARGUMENT FOR DOING IT IS THE GATE, NOT THE FILES. Close-out asserts "the staging
folder is empty for this epic". An epic that deleted its folder WITHOUT folding anything
in passes that check today: empty is empty either way. With an archive it asserts *empty
AND the archive entry exists*, so "folded in" and "silently discarded" stop being
indistinguishable.

WRITE-ONCE IS WHAT MAKES IT SAFE. A stale PLAN document is dangerous because it makes
present-tense claims about the system. An archived proposal claims only "this is what epic
X proposed on this date", which stays true forever. Snapshots cannot rot; only documents
with present-tense claims can.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from models.project import ProjectError, load
from models.resolve import REPO

STAMP = re.compile(r"^---\n", re.M)


def _stamp_frontmatter(path: Path, when: str, epic: str) -> None:
    """Mark a file as archived, in the file itself.

    A reader who opens one of these must not have to know where it sits to know it is
    history — the directory says so, but the file travels.
    """
    text = path.read_text()
    fields = f"status: archived\narchived: {when}\narchived_from_epic: {epic}\n"
    if text.startswith("---\n"):
        head, sep, rest = text[4:].partition("\n---\n")
        path.write_text(f"---\n{head}\n{fields}---\n{rest}")
    else:
        path.write_text(f"---\n{fields}---\n\n{text}")


def archive_epic(epic: str, slug: str = "") -> Path:
    """`git mv` the epic's staging folder into the archive, stamped and dated."""
    project = load()
    archive = project.archive_dir()
    if not archive:
        raise ProjectError(
            "paths.archive is not declared, so there is nowhere to archive to. Declare it "
            "outside paths.docs, paths.proposed and every area — or keep deleting, which "
            "is the current behaviour."
        )
    proposed = (project.paths or {}).get("proposed")
    if not proposed:
        raise ProjectError("harness.yaml declares no paths.proposed")

    matches = sorted((REPO / proposed).glob(f"{epic}*"))
    matches = [m for m in matches if m.is_dir()]
    if not matches:
        raise ProjectError(f"no staging folder for {epic} under {proposed}")
    src = matches[0]

    when = date.today().isoformat()
    dest = REPO / archive / f"{when}-{src.name}"
    if dest.exists():
        raise ProjectError(f"{dest} already exists — this epic was archived already")
    dest.parent.mkdir(parents=True, exist_ok=True)

    moved = subprocess.run(
        ["git", "mv", str(src), str(dest)], cwd=str(REPO), capture_output=True, text=True
    )
    if moved.returncode != 0:
        # Not tracked (a folder created this session and never committed) — a plain move
        # is correct there, and refusing would leave the folder behind for the close-out
        # gate to trip on.
        src.rename(dest)

    for f in sorted(dest.rglob("*.md")):
        _stamp_frontmatter(f, when, epic)
    return dest


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("usage: archive-epic.sh <epic-id>", file=sys.stderr)
        return 2
    try:
        dest = archive_epic(args[0])
    except ProjectError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(dest.relative_to(REPO))
    print(
        "Archived, stamped and dated. It is WRITE-ONCE: a snapshot of what was proposed "
        "stays true, and editing one turns it back into a competing source of truth."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
