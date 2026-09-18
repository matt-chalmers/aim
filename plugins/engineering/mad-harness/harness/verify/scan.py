"""Answer many search questions in ONE tool call.

WHY. Across four real lens runs, 92 bash calls were recorded and 31 of them —
the single largest cluster — were `git grep` / `git log` / `git ls-files`
searches. At swarm.md's measured ~2,600 tokens per tool call, an agent asking
eight separate "does X still appear anywhere?" questions spends ~21,000 tokens on
call overhead alone, for answers that are mostly the single word "no".

This is the batching primitive that does not need programmatic tool calling: the
shell already composes, so one invocation takes N patterns and returns N answers.

A ZERO-HIT PATTERN IS A RESULT, NOT AN ABSENCE. A lens asking "no stale
`scripts/` path remains anywhere tracked" is looking for `hits=0`, so a silent
skip of a pattern would read as a pass. Every pattern given is echoed back with
its count, whether or not it matched.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass

from models.resolve import CHECKOUT

#: Per-pattern cap on printed matches. A batch that dumps everything trades call
#: overhead for output tokens and can be a net loss; the count is always exact,
#: only the listing is capped, and the elision is stated.
DEFAULT_MAX = 12
#: Match lines are truncated to this; a minified bundle or a lockfile can carry a
#: single multi-kilobyte line that would swamp the whole batch.
LINE_CAP = 200


@dataclass
class Result:
    pattern: str
    hits: int
    files: int
    shown: list[str]
    error: str = ""


def scan_one(pattern: str, rev: str | None, pathspec: list[str], cap: int) -> Result:
    cmd = ["git", "grep", "-n", "-I", "-E", pattern]
    if rev:
        cmd.append(rev)
    if pathspec:
        cmd += ["--", *pathspec]
    proc = subprocess.run(cmd, cwd=str(CHECKOUT), capture_output=True, text=True)
    # git grep exits 1 for "no matches" — that is a legitimate answer, not a failure.
    if proc.returncode not in (0, 1):
        return Result(pattern, 0, 0, [], proc.stderr.strip()[:200] or "git grep failed")
    lines = [ln for ln in proc.stdout.splitlines() if ln]
    files = {ln.split(":", 2)[0] for ln in lines}
    shown = [ln[:LINE_CAP] for ln in lines[:cap]]
    return Result(pattern, len(lines), len(files), shown)


def render(
    results: list[Result], cap: int, rev: str | None, pathspec: list[str]
) -> str:
    out: list[str] = []
    scope = f"rev={rev or 'working tree'}"
    if pathspec:
        scope += f" paths={' '.join(pathspec)}"
    out.append(f"# scan — {len(results)} patterns · {scope}")
    out.append("")
    for r in results:
        if r.error:
            out.append(f"## {r.pattern}\n  !! ERROR: {r.error}")
            out.append("")
            continue
        head = f"## {r.pattern}\n  hits={r.hits} files={r.files}"
        if r.hits == 0:
            # Spelled out because "no output" and "no matches" are the same shape
            # in a terminal, and a lens must be able to tell them apart.
            out.append(
                head + "  (NO MATCHES — the pattern was searched and found nothing)"
            )
            out.append("")
            continue
        out.append(head)
        out += [f"    {ln}" for ln in r.shown]
        if r.hits > len(r.shown):
            out.append(f"    ... {r.hits - len(r.shown)} more not shown (cap={cap})")
        out.append("")
    clean = sum(1 for r in results if not r.error and r.hits == 0)
    out.append(f"-- {clean}/{len(results)} patterns had zero hits")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="scan.sh",
        description="Run many searches in one call. Every pattern is answered, "
        "including with zero hits.",
    )
    ap.add_argument(
        "-e",
        "--pattern",
        action="append",
        default=[],
        required=True,
        help="an ERE pattern; repeat for each question you have",
    )
    ap.add_argument(
        "--rev", default=None, help="search a commit instead of the working tree"
    )
    ap.add_argument(
        "--max",
        type=int,
        default=DEFAULT_MAX,
        help=f"matches shown per pattern (default {DEFAULT_MAX})",
    )
    ap.add_argument(
        "pathspec", nargs="*", help="optional git pathspecs to limit the search"
    )
    args = ap.parse_args(argv)

    results = [scan_one(p, args.rev, args.pathspec, args.max) for p in args.pattern]
    print(render(results, args.max, args.rev, args.pathspec))
    return 2 if any(r.error for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
