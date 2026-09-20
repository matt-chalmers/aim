"""Stage an architect's design — `/design` §5 and campaign-loop §3b's staging as one call.

`design.md` under `<paths.proposed>/<epic>-<slug>/` (found or created — the folder
`close-epic.sh` later retires), a draft decision record per `DECISION:` line in the design
with a `decision` task filed for each, and the `ARCHITECTURE:` pointer on the epic. The
prose had the orchestrator compute the folder name (and get it wrong in the direction the
close-out gate refuses), copy the template and paste the result by hand.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from .resolve import HARNESS, REPO

DECISION_LINE = re.compile(r"^\s*DECISION:\s*(?P<q>.+?)\s*$", re.M)


def main(argv: list[str] | None = None) -> int:
    import argparse

    from tracker.staging import draft_adr, ensure_folder, known_prefix, stage_design

    from .project import ProjectError, load

    ap = argparse.ArgumentParser(prog="stage-design.sh", description="Stage an architect's design under the epic's staging folder, a draft decision record per DECISION: line (each filed as a decision task), and the ARCHITECTURE: pointer on the epic.")
    ap.add_argument("epic")
    ap.add_argument("design", help="the architect's result file (dispatch.sh --out)")
    ap.add_argument("--no-decisions", action="store_true", help="stage the design only; file no decision tasks")
    args = ap.parse_args(argv)
    try:
        project = load()
    except ProjectError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    proposed = (project.paths or {}).get("proposed")
    if not proposed:
        print("FAIL: harness.yaml declares no paths.proposed", file=sys.stderr)
        return 2
    try:
        text = Path(args.design).read_text()
    except OSError as exc:
        print(f"FAIL: cannot read {args.design}: {exc}", file=sys.stderr)
        return 2
    import tracker

    store = tracker.task_store()
    epic = store.show(args.epic)
    title = epic.title if epic else ""
    folder = ensure_folder(args.epic, REPO / proposed, title, known_prefix())
    path = stage_design(folder, args.epic, text, title=title, proposed=REPO / proposed)
    print(f"staged {path.relative_to(REPO)}")
    tk = HARNESS / "tracker" / "tk.sh"
    if not args.no_decisions:
        for m in DECISION_LINE.finditer(text):
            q = m.group("q")
            proc = subprocess.run([str(tk), "create", q[:200], "-t", "decision", "-p", "1", "--description", f"Raised by the architect for {args.epic}.\n\n{q}"], capture_output=True, text=True, timeout=120)
            tid = (proc.stdout.strip().splitlines() or ["?"])[-1] if proc.returncode == 0 else "?"
            d = draft_adr(folder, args.epic, q, tid, proposed=REPO / proposed)
            print(f"decision {tid}: {q[:80]} → {d.relative_to(REPO)}")
    proc = subprocess.run([str(tk), "update", args.epic, "--append-notes", f"ARCHITECTURE: design staged at {path.relative_to(REPO)} — folds in at §5"], capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        print(f"note NOT written: {(proc.stderr or proc.stdout).strip()[:200]}", file=sys.stderr)
        return 1
    print(f"noted on {args.epic}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
