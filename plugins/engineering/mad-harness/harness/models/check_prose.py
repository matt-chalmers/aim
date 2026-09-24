"""Catch the wreckage an edit leaves behind in prose.

WHY THIS EXISTS. Three consecutive reviews found the same class of defect: text that
parses, imports and tests clean, but says something broken — a sentence whose subject
was deleted, a rule stated twice, a bullet ending on a comma. Every mechanical guard
the harness has looked straight past them, and `make check` was green with fourteen
present.

They all share one cause: an identifier was removed and the sentence around it was
never read back. The durable fix is not another sweep but a rule for the sweeper —
**when you remove an identifier, read the sentence you leave behind** — and this is
that rule made mechanical, because a rule nobody can forget beats a rule everybody is
told to remember.

Deliberately narrow. Every check here fires on a shape that is almost never
intentional in this corpus, because a prose linter that cries wolf gets disabled and
then catches nothing at all.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .resolve import AGENTS_DIR, _prompts_dir


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    rule: str
    detail: str
    text: str


#: Fenced code, tables and link-heavy lines have their own grammar; prose rules do not
#: apply inside them.
def _prose_lines(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    fenced = False
    lines = text.splitlines()
    # YAML frontmatter has its own grammar — `tools: Read, Grep` is not a sentence.
    start = 0
    if lines and lines[0].strip() == "---":
        for j in range(1, len(lines)):
            if lines[j].strip() == "---":
                start = j + 1
                break
    for i, raw in enumerate(lines[start:], start + 1):
        s = raw.rstrip()
        if s.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced or not s.strip():
            continue
        if s.lstrip().startswith(("|", ">", "---", "==")):
            continue
        out.append((i, s))
    return out


def _sentences(s: str) -> list[str]:
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+", s) if x.strip()]


def check_text(path: Path, text: str) -> list[Finding]:
    found: list[Finding] = []
    lines = _prose_lines(text)
    raw_lines = text.splitlines()

    for idx, (n, line) in enumerate(lines):
        stripped = line.strip()

        # 1. The same sentence twice in a row — "Docs only. Docs only."
        sents = _sentences(stripped)
        for a, b in zip(sents, sents[1:]):
            if len(a) > 6 and a.lower() == b.lower():
                found.append(
                    Finding(
                        path,
                        n,
                        "repeated-sentence",
                        "the same sentence appears twice in a row",
                        stripped,
                    )
                )
                break

        # 2. Doubled interior spaces — the fingerprint of a deleted phrase.
        #    Bullets were exempted WHOLESALE, which is why this missed the worst content
        #    loss of the third review: the gutted `SURFACE:` example was a list item.
        #    The exemption exists for alignment after the marker, so it now applies only
        #    there — everything past the marker and its first word is checked as prose.
        body = (
            re.sub(r"^\s*([-*]|\d+\.)\s+\S+\s*", "", stripped)
            if re.match(r"^\s*([-*]|\d+\.)\s", stripped)
            else stripped
        )
        # Spaces inside an inline code span are content, not spacing — a snippet like
        # `    x += f(a)` is demonstrating indentation.
        body = re.sub(r"`[^`]*`", "``", body)
        if re.search(r"\S {2,}\S", body) and not stripped.startswith("#"):
            found.append(
                Finding(
                    path,
                    n,
                    "double-space",
                    "two spaces mid-line, usually where a phrase was cut",
                    stripped,
                )
            )

        # 3. A line ending on a comma, followed by a blank line or a new bullet or
        #    heading: the sentence lost its ending.
        if stripped.endswith(","):
            nxt = raw_lines[n] if n < len(raw_lines) else ""
            # A list marker is "- " or "* " with a space; "**bold**" continuing the
            # sentence is not a new block, and a bullet may legitimately end on a comma
            # when the next bullet continues the list.
            new_block = bool(re.match(r"^\s*([-*]\s|#|\d+\.\s)", nxt))
            if not nxt.strip() and not new_block:
                found.append(
                    Finding(
                        path,
                        n,
                        "dangling-comma",
                        "sentence ends on a comma before a break",
                        stripped,
                    )
                )

        # 4. A wrapped line starting lowercase right after one that ended in a full
        #    stop: two fragments spliced where a sentence was removed between them.
        if (
            idx
            and re.match(r"^[a-z]", stripped)
            and not stripped.startswith(("http", "www"))
        ):
            prev_n, prev = lines[idx - 1]
            if prev_n == n - 1 and prev.rstrip().endswith((".", ".*", ".**")):
                found.append(
                    Finding(
                        path,
                        n,
                        "orphan-fragment",
                        "lowercase continuation after a completed sentence",
                        stripped,
                    )
                )

        # 5. THE SAME WORD ending one line and starting the next. The signature of a
        #    substitution applied twice, or of a phrase deleted from between them.
        #    A new bullet or heading legitimately reuses the previous line's last word
        #    ("...as a single commit." / "- Commit message: ..."), so those are skipped.
        starts_block = bool(re.match(r"^([-*#>]|\d+\.)\s", stripped))
        if idx and not starts_block:
            prev_n, prev = lines[idx - 1]
            if prev_n == n - 1:
                # The ACTUAL last and first tokens, not merely the last long one — an
                # earlier version compared "the" against a word four places back and
                # reported three passages that were perfectly well formed.
                last = re.findall(r"[A-Za-z][A-Za-z'-]*", prev.rstrip("*_`.,;:— -"))
                first = re.match(r"[^A-Za-z]*([A-Za-z][A-Za-z'-]*)", stripped)
                if (
                    last
                    and first
                    and len(last[-1]) > 3
                    and last[-1].lower() == first.group(1).lower()
                    # A deliberately repeated word inside one quoted phrase is not a
                    # defect: "tests later — later never comes".
                    and "—" not in prev[-3:]
                ):
                    found.append(
                        Finding(
                            path,
                            n,
                            "repeated-across-wrap",
                            f"{first.group(1)!r} ends the previous line and starts this one",
                            stripped,
                        )
                    )

        # 6. The same word twice in a row on one line — a substitution applied over its
        #    own output ("`security.invariants` it invariants its"). Rule 5 sees this
        #    only across a wrap; on a single line both guards looked straight past it.
        # 6. A word repeated on one line — adjacent ("the the"), or with one short
        #    filler between it ("invariants it invariants its"), which is the shape a
        #    substitution applied over its own output leaves. Rule 5 sees this only
        #    across a wrap; on a single line both guards looked straight past it.
        #
        #    English has genuine X-filler-X idioms — side by side, back to back, hand in
        #    hand, word for word — so a prepositional filler is excluded rather than
        #    reported. The trailing guard matters too: without it "the decorative test
        #    `test-doctrine`" matches, because `test` is a prefix at a word boundary.
        IDIOM_FILLER = {"by", "to", "for", "in", "on", "of", "and", "or", "a", "the"}
        # Function words legitimately recur inside one clause — "the lanes that do that
        # work", "check what it is that they meant". Only the ADJACENT case is a defect
        # for these, and that is handled below.
        COMMON = {
            "that",
            "this",
            "what",
            "which",
            "when",
            "where",
            "there",
            "their",
            "with",
            "from",
            "have",
            "been",
            "were",
            "does",
            "will",
            "would",
            "they",
            "them",
            "then",
            "than",
            "some",
            "such",
            "into",
            "each",
        }
        for m in re.finditer(
            r"(?<![\w'-])([A-Za-z][A-Za-z']{1,})(?![\w'-])[\s`*_]+(\w{1,3}[\s`*_]+)?"
            r"\1(?![\w'-])",
            stripped,
            re.I,
        ):
            filler = (m.group(2) or "").strip("`*_ ").lower()
            if filler and (
                filler in IDIOM_FILLER
                or len(m.group(1)) < 4
                or m.group(1).lower() in COMMON
            ):
                continue
            # Two-letter adjacents are usually a real construction — "it lands
            # in** in the frontmatter's list" is awkward, not broken.
            if not filler and len(m.group(1)) < 3:
                continue
            found.append(
                Finding(
                    path,
                    n,
                    "repeated-word",
                    f"{m.group(1)!r} appears twice in a row",
                    stripped,
                )
            )
            break

    return found


def scan() -> list[Finding]:
    out: list[Finding] = []
    seen: set[Path] = set()
    # SCOPE, AND WHY IT STOPS WHERE IT DOES.
    #
    # Markdown prose, everywhere the plugin keeps any: the three prompt directories, the
    # harness's own notes, `docs/`, and the top-level README.
    #
    # `docs/` WAS OUT OF SCOPE UNTIL 0.10.35, which is backwards: it is the largest body of
    # prose here and the one people read end to end. It was clean when first scanned (0
    # findings over 30 files), so nothing was owed — but a corpus that is only correct
    # because nobody has edited it lately is one rule away from the defect class this
    # check exists to catch, and generated tables inside it are rewritten by a script.
    #
    # NOT shell, Python or YAML comments, though an earlier pass claimed to widen scope
    # to `templates/` and quietly did nothing — the template is `.yaml`, so zero files
    # matched, which is the empty-reads-as-clean failure this repo warns about, committed
    # inside the fix for it. Measured before excluding them: 268 findings across 547
    # files, essentially all false. Comment blocks in scripts and config use ALIGNED
    # COLUMNS — usage tables, trailing explanations — so the double-space rule inverts
    # there: the alignment is the content. A linter that cries wolf gets disabled and
    # then catches nothing, so those file types are out until a rule set that suits them
    # exists.
    from .resolve import HARNESS, PLUGIN_ROOT

    roots = [
        AGENTS_DIR,
        _prompts_dir("commands"),
        _prompts_dir("skills"),
        HARNESS,
        PLUGIN_ROOT / "docs",
        PLUGIN_ROOT,  # top-level README and any sibling prose; rglob is bounded by *.md
    ]
    for d in roots:
        if not d.is_dir():
            continue
        found = sorted(d.glob("*.md")) if d == PLUGIN_ROOT else sorted(d.rglob("*.md"))
        for p in found:
            if p in seen:
                continue
            seen.add(p)
            out += check_text(p, p.read_text())
    return out


def _scanned() -> int:
    """How many files the scan actually read. Reported because a scope that silently
    matches nothing is the failure this check exists to catch."""
    from .resolve import HARNESS, PLUGIN_ROOT

    seen: set = set()
    for d in (AGENTS_DIR, _prompts_dir("commands"), _prompts_dir("skills"), HARNESS, PLUGIN_ROOT / "docs"):
        if d.is_dir():
            seen |= set(d.rglob("*.md"))
    seen |= set(PLUGIN_ROOT.glob("*.md"))
    return len(seen)


def main() -> int:
    findings = scan()
    if not findings:
        print(f"OK — no broken prose in {_scanned()} markdown files.")
        return 0
    by_rule: dict[str, int] = {}
    for f in findings:
        by_rule[f.rule] = by_rule.get(f.rule, 0) + 1
    for f in findings:
        print(f"{f.path.parent.name}/{f.path.name}:{f.line}  [{f.rule}]  {f.detail}")
        print(f"    {f.text[:100]}")
    print(
        f"\nFAIL: {len(findings)} broken passages — "
        + ", ".join(f"{k} {v}" for k, v in sorted(by_rule.items())),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
