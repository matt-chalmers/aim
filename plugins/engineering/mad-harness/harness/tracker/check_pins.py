"""Which records cite code by line number, and which of those pins have drifted.

WHY THIS EXISTS. A line pin is correct only until somebody edits above it — and the
failure that costs time is not an obvious miss, it is a pin that lands on
PLAUSIBLE-LOOKING WRONG TEXT. A reader arrives somewhere that reads like an answer and
believes it. In one wave a lens's only blocking finding was exactly this: two pins in
another record had drifted onto unrelated code, and the lens found them BY HAND. An agent
can only check the pins it thinks to check; this checks all of them.

A REPORTING TOOL, NOT A GATE. It cannot know whether a pin is still correct — only
whether the file moved under it. DEAD is certain; SUSPECT is a candidate that needs eyes.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import tracker

#: path:line or path:line-line, where the path looks like a real source or doc file.
PIN = re.compile(
    r"\b((?:[\w./-]+/)?[\w.-]+\.(?:py|md|ts|tsx|js|jsx|sh|yaml|yml|toml)):(\d+)(?:-(\d+))?"
)

_mtime: dict[str, str] = {}
_tracked: list[str] | None = None


def _last_touched(path: str) -> str:
    if path not in _mtime:
        r = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--", path],
            capture_output=True,
            text=True,
        )
        _mtime[path] = r.stdout.strip()
    return _mtime[path]


def _resolve(path: str) -> str | None:
    """Records cite paths loosely — `services/x.py`, or a bare filename.

    Resolve by suffix against tracked files so a loose citation is not reported as rot.
    An AMBIGUOUS suffix resolves to nothing rather than to a guess: a wrong resolution
    would print wrong line content, which is the failure this tool exists to prevent.
    """
    global _tracked
    if os.path.exists(path):
        return path
    if _tracked is None:
        _tracked = subprocess.run(
            ["git", "ls-files"], capture_output=True, text=True
        ).stdout.split()
    hits = [f for f in _tracked if f == path or f.endswith("/" + path)]
    return hits[0] if len(hits) == 1 else None


def _line_at(path: str, n: int) -> str | None:
    try:
        with open(path, errors="replace") as fh:
            for i, line in enumerate(fh, 1):
                if i == n:
                    return line.rstrip()[:96]
    except OSError:
        return None
    return None  # file shorter than the pin


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    only = args[0] if args else ""

    store = tracker.task_store()
    try:
        tasks = store.list(status="open", limit=900)
    except tracker.TrackerError as exc:
        print(f"could not read the record list: {exc}", file=sys.stderr)
        return 2
    if only:
        tasks = [t for t in tasks if t.id == only]

    dead, suspect, n_pins, n_records = [], [], 0, 0
    for t in tasks:
        text = f"{t.description}\n{t.notes}"
        pins = sorted({(m.group(1), int(m.group(2))) for m in PIN.finditer(text)})
        if not pins:
            continue
        n_records += 1
        for cited, line in pins:
            n_pins += 1
            real = _resolve(cited)
            if real is None:
                dead.append((t.id, cited, line, "file does not exist"))
                continue
            content = _line_at(real, line)
            if content is None:
                dead.append((t.id, cited, line, f"file has fewer than {line} lines"))
                continue
            touched = _last_touched(real)
            if touched and t.updated_at and touched > t.updated_at:
                suspect.append((t.id, real, line, touched[:10], content))

    print(f"=== line-pin sweep: {n_pins} pins across {n_records} open records ===\n")
    if dead:
        print(f"--- DEAD ({len(dead)}) — the pin cannot resolve at all. Certain rot. ---")
        for tid, path, line, why in dead:
            print(f"  {tid:22s} {path}:{line}  — {why}")
        print()
    if suspect:
        print(
            f"--- SUSPECT ({len(suspect)}) — the file was committed AFTER the record was "
            f"last updated. ---"
        )
        print("    The text below is what that line says NOW. If it is not what the record")
        print("    meant, the pin has drifted. RE-PIN BY SYMBOL — never refresh the number.\n")
        for tid, path, line, when, content in suspect:
            print(f"  {tid:22s} {path}:{line}   (file touched {when})")
            print(f"  {'':22s}   now reads: {content}")
        print()
    if not dead and not suspect:
        print("  No pin resolves to a moved or missing file. Nothing to re-pin.\n")

    print("A SUSPECT is not a defect — most files move without invalidating the intent.")
    print("But a SUSPECT you did not read is how a record comes to cite plausible wrong text.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
