"""Keep the harness-conventions block identical across the skills that carry it.

WHY TWO COPIES AT ALL. The rules — task glosses, cite-by-symbol — apply to every agent
that writes a task or a report, and those agents preload no single skill in common:
`evidence-gathering` reaches every lens, reader and (since 0.10.5) writer; `spec-editor`
preloads only `spec-lifecycle`. There were three carriers while the writers preloaded
only `worker-protocol`; once they took `evidence-gathering` its copy was paid twice on
every writer dispatch (0.10.6), so it went. Coverage is CHECKED, below — an agent whose
preloads include no carrier fails the check rather than silently losing the rules.

WHY A CHECK. Three statements of one rule is the shape that produced most of what four
independence reviews found. Duplication is only safe when divergence is mechanical.
"""

from __future__ import annotations

import sys

from .resolve import _prompts_dir

START = "<!-- HARNESS CONVENTIONS:"
END = "<!-- END HARNESS CONVENTIONS -->"

CARRIERS = ("evidence-gathering", "spec-lifecycle")


def extract(text: str) -> str | None:
    if START not in text or END not in text:
        return None
    return text[text.index(START) : text.index(END) + len(END)]


def _uncovered(carriers: set[str]) -> list[str]:
    """Agents that preload skills but none of the carriers."""
    import re

    out = []
    for path in sorted(_prompts_dir("agents").glob("*.md")):
        text = path.read_text()
        head = text[: text.index("\n---\n", 3)]
        declared = set(re.findall(r"^\s+- (\S+)$", head, re.M))
        if declared and not (declared & carriers):
            out.append(f"{path.stem} preloads {sorted(declared)}")
    return out


def main() -> int:
    skills = _prompts_dir("skills")
    blocks: dict[str, str] = {}
    missing: list[str] = []

    for name in CARRIERS:
        path = skills / name / "SKILL.md"
        if not path.is_file():
            missing.append(f"{name}: no SKILL.md")
            continue
        block = extract(path.read_text())
        if block is None:
            missing.append(f"{name}: carries no HARNESS CONVENTIONS block")
        else:
            blocks[name] = block

    if missing:
        print("FAIL:", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        print(
            "\n  Every agent must reach these rules through a skill it preloads. Losing\n"
            "  one carrier silently drops them for whichever agents preload only that one.",
            file=sys.stderr,
        )
        return 1

    uncovered = _uncovered(set(blocks))
    if uncovered:
        print("FAIL: these agents preload no skill carrying the harness conventions:", file=sys.stderr)
        for u in uncovered:
            print(f"  - {u}", file=sys.stderr)
        return 1
    distinct = set(blocks.values())
    if len(distinct) > 1:
        print(
            "FAIL: the conventions block has diverged across its carriers:",
            file=sys.stderr,
        )
        for name, b in blocks.items():
            print(
                f"  {name:22s} {len(b):>5} chars  sha={hash(b) & 0xFFFFFF:06x}",
                file=sys.stderr,
            )
        print(
            "\n  Edit every carrier or none. A rule stated two ways is worse than a rule\n"
            "  stated once badly, because a reader cannot tell which one is live.",
            file=sys.stderr,
        )
        return 1

    n = len(next(iter(distinct)))
    print(f"OK — conventions block identical across {len(blocks)} skills ({n} chars).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
