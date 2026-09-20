"""The staged-folder edits `spec-editor` was doing by hand — line-anchored, as one call each.

WHAT THIS REPLACES. Fold-in ① and ② asked the spec-editor for three mechanical edits and
warned it about each: extract `## Acceptance criteria` ANCHORED TO LINE START, because an
unanchored match once landed inside a sentence that quoted the heading and folded in
nothing, silently; flip the proposal's frontmatter to `status: folded-in` with the date;
and `git mv` a resolved draft decision record to `paths.adrs` at the number from
`tk.sh adr-next` with its status and decision filled in — the recorded collision was two
streams numbering independently. A warning in prose is a request; `tracker.staging`'s
functions are the rule. This is their command line.

    staged.sh section <file> "<heading>"                  the body under that heading; exit 1 absent, 2 duplicated
    staged.sh set-status <file> <status> [k=v ...]        frontmatter in place; `folded_in` defaults to today for folded-in
    staged.sh promote-adr <draft> --decision "<text>"     move to paths.adrs at adr-next; prints the destination
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from tracker.staging import promote_adr, section, set_status

from .project import ProjectError, load
from .resolve import REPO

EXIT_OK, EXIT_ABSENT, EXIT_USAGE = 0, 1, 2


def _caller(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (REPO / path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="staged.sh", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    s = sub.add_parser("section", help="the body under a heading, line-anchored")
    s.add_argument("file")
    s.add_argument("heading")
    st = sub.add_parser("set-status", help="flip frontmatter status in place")
    st.add_argument("file")
    st.add_argument("status")
    st.add_argument("also", nargs="*", help="extra frontmatter keys as k=v")
    pa = sub.add_parser("promote-adr", help="move a resolved draft decision record into paths.adrs")
    pa.add_argument("draft")
    g = pa.add_mutually_exclusive_group(required=True)
    g.add_argument("--decision", help="the owner's settlement, verbatim")
    g.add_argument("--decision-file", help="a file holding it")
    args = ap.parse_args(argv)

    if args.verb == "section":
        path = _caller(args.file)
        if not path.is_file():
            print(f"no such file: {path}", file=sys.stderr)
            return EXIT_USAGE
        try:
            body = section(path.read_text(), args.heading)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_USAGE
        if body is None:
            print(f"no `{args.heading}` heading in {path} — a proposal without one folds in nothing; report it, do not guess", file=sys.stderr)
            return EXIT_ABSENT
        print(body)
        return EXIT_OK

    if args.verb == "set-status":
        path = _caller(args.file)
        if not path.is_file():
            print(f"no such file: {path}", file=sys.stderr)
            return EXIT_USAGE
        also: dict[str, str] = {}
        for kv in args.also:
            k, sep, v = kv.partition("=")
            if not sep or not k:
                print(f"extra keys are k=v, not {kv!r}", file=sys.stderr)
                return EXIT_USAGE
            also[k] = v
        if args.status == "folded-in":
            also.setdefault("folded_in", date.today().isoformat())
        set_status(path, args.status, **also)
        print(f"{path}: status: {args.status}" + "".join(f", {k}: {v}" for k, v in also.items()))
        return EXIT_OK

    draft = _caller(args.draft)
    if not draft.is_file():
        print(f"no such draft: {draft}", file=sys.stderr)
        return EXIT_USAGE
    try:
        adrs = (load().paths or {}).get("adrs")
    except ProjectError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    if not adrs:
        print("harness.yaml declares no paths.adrs — nowhere to promote a decision record to", file=sys.stderr)
        return EXIT_USAGE
    decision = args.decision if args.decision is not None else _caller(args.decision_file).read_text()
    if not decision.strip():
        print("an empty decision — a record promoted with no settlement is an unresolved draft under a number", file=sys.stderr)
        return EXIT_USAGE
    dest = promote_adr(draft, REPO / adrs, decision=decision, cwd=REPO)
    print(dest)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
