"""Read many files, or many slices of files, in ONE tool call.

WHY. The second and third largest clusters in four real lens runs were reading
slices of files (16 calls: sed/head/tail/cat) and reading files at a revision
(11 calls: `git show <sha>:<path>`) — 27 of 92, ~29%. Each is ~2,600 tokens of
call overhead for content the agent already knew it wanted. Batching them costs
nothing in content tokens and saves the overhead on all but the first.

A MISSING PATH IS REPORTED, NEVER SKIPPED. An agent that asked for six files and
silently received five will reason from the five and conclude something about the
sixth. Every spec given produces a stanza, including `!! not found`.

Line numbers are emitted so a lens can cite precisely in its report. (CLAUDE.md
bans line pins in *tasks*, which are read months later; a lens verdict is read
within minutes and the pin is what makes it checkable.)
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass

from models.resolve import REPO

#: Lines shown per spec before eliding. A bare path on a 3,000-line file would
#: otherwise swamp the batch and turn a saving into a loss.
DEFAULT_MAX = 120
#: Total across the whole batch, so `peek *.py` cannot blow the context window.
TOTAL_CAP = 1200
#: Long lines (minified bundles, lockfiles) get truncated rather than wrapped.
LINE_CAP = 400

SPEC = re.compile(r"^(?P<path>[^:]+?)(?::(?P<start>\d+)(?:-(?P<end>\d+))?)?$")


@dataclass
class Chunk:
    path: str
    start: int
    end: int
    total: int
    lines: list[str]
    error: str = ""


def _read(path: str, rev: str | None) -> tuple[list[str], str]:
    if rev:
        # `<rev>:<path>` is resolved from the REPOSITORY ROOT, not from `cwd` — so a
        # plugin nested inside a larger repository (a marketplace, a monorepo) would
        # look up `<outer-root>/harness/...` and report every file missing at that
        # revision. `<rev>:./<path>` is the cwd-relative form, which agrees with the
        # working-tree branch below and is identical when REPO *is* the root.
        proc = subprocess.run(
            ["git", "show", f"{rev}:./{path}"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return [], f"not found at {rev}"
        return proc.stdout.splitlines(), ""
    p = REPO / path
    try:
        return p.read_text().splitlines(), ""
    except FileNotFoundError:
        return [], "not found"
    except IsADirectoryError:
        return [], "is a directory"
    except UnicodeDecodeError:
        return [], "binary file"


def peek_one(spec: str, rev: str | None, cap: int) -> Chunk:
    m = SPEC.match(spec.strip())
    if not m:
        return Chunk(
            spec, 0, 0, 0, [], "unparseable spec (want path, path:N, or path:N-M)"
        )
    path = m.group("path")
    lines, err = _read(path, rev)
    if err:
        return Chunk(path, 0, 0, 0, [], err)

    total = len(lines)
    start = int(m.group("start") or 1)
    end = int(m.group("end") or (start + cap - 1 if m.group("start") else total))
    start, end = max(1, start), min(total, end)
    if start > total:
        return Chunk(path, start, end, total, [], f"file has only {total} lines")
    body = lines[start - 1 : end][:cap]
    return Chunk(
        path, start, start + len(body) - 1, total, [ln[:LINE_CAP] for ln in body]
    )


def render(chunks: list[Chunk], rev: str | None, cap: int) -> str:
    out = [
        f"# peek — {len(chunks)} specs · {'rev=' + rev if rev else 'working tree'}",
        "",
    ]
    budget = TOTAL_CAP
    for c in chunks:
        if c.error:
            out += [f"=== {c.path} ===", f"  !! {c.error}", ""]
            continue
        shown = c.lines[: max(0, budget)]
        budget -= len(shown)
        out.append(f"=== {c.path}  (lines {c.start}-{c.end} of {c.total}) ===")
        out += [f"{c.start + i:>6}  {ln}" for i, ln in enumerate(shown)]
        if len(shown) < len(c.lines):
            out.append(
                f"       ... {len(c.lines) - len(shown)} more elided (batch cap {TOTAL_CAP})"
            )
        elif c.end < c.total:
            out.append(
                f"       ... file continues to line {c.total} (per-spec cap {cap})"
            )
        out.append("")
    missing = [c.path for c in chunks if c.error]
    out.append(
        f"-- {len(chunks) - len(missing)}/{len(chunks)} specs read"
        + (f"; FAILED: {', '.join(missing)}" if missing else "")
    )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="peek.sh",
        description="Read many files or slices in one call. SPEC is path, path:N, or path:N-M.",
    )
    ap.add_argument("spec", nargs="+")
    ap.add_argument(
        "--rev", default=None, help="read at a commit instead of the working tree"
    )
    ap.add_argument(
        "--max",
        type=int,
        default=DEFAULT_MAX,
        help=f"lines per spec (default {DEFAULT_MAX})",
    )
    args = ap.parse_args(argv)

    chunks = [peek_one(s, args.rev, args.max) for s in args.spec]
    print(render(chunks, args.rev, args.max))
    return 2 if any(c.error for c in chunks) else 0


if __name__ == "__main__":
    sys.exit(main())
