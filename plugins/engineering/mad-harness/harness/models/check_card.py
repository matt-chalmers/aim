"""The orchestrator card: one block, byte-identical in every command, under budget.

WHY A CARD. A session becomes an orchestrator by running a command — /campaign, /swarm,
/grind, /plan-swarm and the rest — and the command's text is what that session has loaded
at that moment. The rules that make an orchestrator cheap (delegate reading, one call not
five, artefacts by path) had been stated once, in campaign-loop §0: reached only by
/campaign, arriving as a message, and measured (0.10.7) as the kind of text a compaction
discards. A card is the stack-card pattern one level up — a budgeted block in the prompt,
doctrine on demand — and `swarm/pinned.sh` prints it again after any compaction or resume,
the moment it is lost.

WHY A CHECK. Ten copies of one block is the shape that produced most of what four
independence reviews found; duplication is safe only when divergence is mechanical.
`harness/orchestrator-card.md` is the canonical copy; every command must carry it exactly.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .check_skills import CARD_BUDGET_CHARS
from .resolve import HARNESS, _prompts_dir

START = "<!-- ORCHESTRATOR CARD"
END = "<!-- END ORCHESTRATOR CARD -->"
CANONICAL = HARNESS / "orchestrator-card.md"


def body(text: str) -> str | None:
    """The card between its markers, without the marker comments, or None if absent."""
    if START not in text or END not in text:
        return None
    inner = text[text.index(START) : text.index(END)]
    inner = inner[inner.index("-->") + 3 :] if "-->" in inner else inner
    return inner.strip()


def canonical() -> str:
    card = body(CANONICAL.read_text())
    if card is None:
        raise RuntimeError(f"{CANONICAL} carries no ORCHESTRATOR CARD block")
    return card


def carriers() -> list[Path]:
    """Every command — a session becomes an orchestrator through any of them."""
    return sorted(_prompts_dir("commands").glob("*.md"))


def problems() -> list[str]:
    out: list[str] = []
    card = canonical()
    if len(card) > CARD_BUDGET_CHARS:
        out.append(f"the card is {len(card):,} chars; budget {CARD_BUDGET_CHARS:,} — it is paid on every orchestrator command and every compaction")
    for path in carriers():
        found = body(path.read_text())
        if found is None:
            out.append(f"{path.name}: carries no ORCHESTRATOR CARD block")
        elif found != card:
            out.append(f"{path.name}: its card differs from {CANONICAL.name} ({len(found)} vs {len(card)} chars)")
    return out


def main() -> int:
    bad = problems()
    if bad:
        print("FAIL:", file=sys.stderr)
        for b in bad:
            print(f"  - {b}", file=sys.stderr)
        print(
            "\n  Edit harness/orchestrator-card.md, then `check-orchestrator-card.sh --write` "
            "to copy it into every command. A rule stated ten ways is worse than one stated once.",
            file=sys.stderr,
        )
        return 1
    n = len(carriers())
    print(f"OK — orchestrator card identical across {n} commands ({len(canonical())} chars, budget {CARD_BUDGET_CHARS:,}).")
    return 0


def write() -> int:
    """Copy the canonical card into every command: replace an existing block, or insert
    one after the frontmatter."""
    src = CANONICAL.read_text()
    block = src[src.index(START) : src.index(END) + len(END)]
    # The per-command marker names where the copy came from, so a reader of the command
    # knows not to edit it there.
    block = block[: block.index("-->")].rstrip() + " -->" + block[block.index("-->") + 3 :]
    block = (
        "<!-- ORCHESTRATOR CARD: mirrored from harness/orchestrator-card.md by\n"
        "     check-orchestrator-card.sh --write; edit it there, never here. -->"
        + block[block.index("-->") + 3 :]
    )
    for path in carriers():
        text = path.read_text()
        if START in text and END in text:
            text = text[: text.index(START)] + block + text[text.index(END) + len(END) :]
        else:
            fm_end = text.index("\n---\n", 3) + len("\n---\n") if text.startswith("---") else 0
            text = text[:fm_end] + "\n" + block + "\n" + text[fm_end:]
        path.write_text(text)
        print(f"wrote {path.name}")
    return main()


if __name__ == "__main__":
    raise SystemExit(write() if "--write" in sys.argv[1:] else main())
