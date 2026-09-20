"""Assemble a writer's prompt from what the tracker and the harness already hold.

WHAT THE ORCHESTRATOR DID BY HAND, N TIMES PER WAVE. `/swarm` step 5 said each prompt
"must carry, in full": the task's complete `tk.sh show` text, that tests go through
`run.sh`, the per-worker environment "as `.swarm-env` actually generated it — read that
file rather than retyping it; a hand-typed copy is how a worker ends up sharing a
sibling's resources", the commit protocol and the merge-slot id, the resource ban list,
the ten-line return contract, the task's slice of the epic's SPEC INDEX, the memory keys
already known to be relevant, and a fidelity task's measured defect list. Nine items,
seven of them derivable, one Write per worker at the orchestrator's context price — and
"the lens failures that cost this campaign most were tasks whose worker never knew which
ADR or owner decision bound them", which is the item most often dropped.

WHAT IS NOT HERE, AND WHY. The commit protocol, the ban list and the return contract are
`worker-protocol` doctrine, in the worker's system prompt on every dispatch since
0.10.18; the technology card, the whereabouts, the answered permission requests and the
conventions are `dispatch.with_context`. Repeating any of them in the message is a second
copy that drifts. What the orchestrator ADDS — a trap specific to this diff, anything the
tracker does not hold — goes in `--prompt-extra`.

THE SPEC INDEX SLICE IS POINTERS. The epic's `spec-index.md` (staged by the survey) is
filtered to the entries the task's `SURFACE:` and `AUTHORITATIVE SPEC` lines name; the
worker opens the doc, because a paraphrase is how a task gets built from a summary. No
index, or no staging folder, is SAID — never silently omitted.
"""

from __future__ import annotations

import re
from pathlib import Path

from .resolve import HARNESS, REPO

SURFACE = re.compile(r"^\s*SURFACE:\s*(?P<t>.*)$", re.I | re.M)
AUTHORITATIVE = re.compile(r"^\s*AUTHORITATIVE SPEC:\s*(?P<t>.*)$", re.I | re.M)
DEFECTS = re.compile(r"^\s*(DEFECTS|MEASURED):", re.M)
PATHISH = re.compile(r"(?:[\w.-]+/)+[\w.-]+")


def _record(task):
    from tracker.cli import _record

    return _record(task)


def spec_slice(parent: str | None, text: str, project) -> str:
    """The lines of the epic's spec-index.md that the task's SURFACE/AUTHORITATIVE lines
    point at — pointers only — or a sentence saying there is none."""
    if not parent:
        return "No SPEC INDEX: the task has no parent epic."
    from tracker.staging import known_prefix, staged_folder

    proposed = (getattr(project, "paths", None) or {}).get("proposed") if project else None
    if not proposed:
        return "No SPEC INDEX: harness.yaml declares no paths.proposed."
    folder, _ = staged_folder(parent, REPO / proposed, known_prefix())
    if folder is None or not (folder / "spec-index.md").is_file():
        return f"No SPEC INDEX for epic {parent} (no staged spec-index.md) — read the task's own SURFACE: line and the feature doc it names."
    index = (folder / "spec-index.md").read_text()
    needles: list[str] = []
    for rx in (SURFACE, AUTHORITATIVE):
        for m in rx.finditer(text):
            needles += [w.strip("`'\",.;:()") for w in m.group("t").split() if len(w.strip("`'\",.;:()")) > 3]
    needles = [n for n in needles if n.lower() not in ("none", "declared", "security", "invariants", "with", "that", "this", "from")]
    hits = []
    for ln in index.splitlines():
        if ln.strip().startswith("#") or not ln.strip():
            continue
        if any(n.lower() in ln.lower() for n in needles) or PATHISH.search(ln) and any(p in ln for p in PATHISH.findall(text)):
            hits.append(ln.rstrip())
    head = f"SPEC INDEX for epic {parent} — {folder / 'spec-index.md'} (pointers; OPEN the docs, never build from a paraphrase):"
    if not hits:
        return head + "\n  (no entry matched the task's SURFACE/AUTHORITATIVE lines — read the whole index at that path; it is short)"
    return head + "\n" + "\n".join(f"  {h}" for h in hits[:24]) + ("\n  …" if len(hits) > 24 else "")


def memory_keys(text: str, title: str, memories) -> list[str]:
    """Memory index lines whose key or first words share a token with the task."""
    tokens = {t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", title + " " + " ".join(PATHISH.findall(text)))}
    tokens -= {"task", "task-", "with", "that", "this", "from", "into", "tests", "test"}
    out = []
    for line in memories or []:
        words = {w.lower().strip("`:,.") for w in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", line)}
        if tokens & words:
            out.append(line.strip()[:160])
    return out[:12]


def build(task_id: str, *, lane: str | None, worker: int | None, extra: str = "", store=None, memories=None, project=None) -> str:
    import tracker

    store = store or tracker.task_store()
    task = store.show(task_id)
    if task is None:
        raise LookupError(f"no task {task_id} in the tracker")
    if project is None:
        from .project import ProjectError, load

        try:
            project = load()
        except ProjectError:
            project = None
    if memories is None:
        try:
            memories = tracker.memory_store().memories()
        except Exception:  # noqa: BLE001
            memories = []
    text = "\n".join(x for x in (task.description, task.acceptance, task.notes) if x)
    lane_arg = f"--lane {lane} " if lane else ""
    parts = [
        f"You are implementing ONE task in an isolated git worktree, as worker {worker or '?'}" + (f" in lane `{lane}`" if lane else "") + ".",
        "",
        "## The task — verbatim from the tracker",
        "",
        "```",
        _record(task),
        "```",
        "",
        "## How to run and commit here",
        "",
        f"- Tests: `{HARNESS}/verify/run.sh {lane_arg}test_scoped <path>` (and `test` for the scoped suite). It loads this worktree's `.swarm-env` — your own per-worker resources — itself. Never `source .swarm-env`, never retype it, never prefix a command with `VAR=value`.",
        f"- Commit: `{HARNESS}/swarm/commit.sh {task.id} -m \"<type>(<scope>): … ({task.id})\" -- <every path you changed>` — one task, one commit, inside the merge slot; it refuses a contaminated index and the tracker's export. Do not push.",
        f"- Claim: your claim on {task.id} was taken by the dispatcher under your actor; `tk.sh claim {task.id}` is idempotent for you. NEVER `tk.sh close` it: the lens gate judges your commit and the orchestrator closes the task after it passes — a task its worker closed is reopened (measured). Put what shipped and how you verified it in your return line and in a `tk.sh note`.",
        "",
        "## What binds this task",
        "",
        spec_slice(task.parent, text, project),
        "",
    ]
    keys = memory_keys(text, task.title, memories)
    if keys:
        parts += ["## Field-guide entries already known to be relevant (`tk.sh recall <key>` for the body)", ""] + [f"- {k}" for k in keys] + [""]
    else:
        parts += ["## Field guide", "", "No index entry matched this task's title or paths; `tk.sh memories` is the index if you need it.", ""]
    if "fidelity" in (task.labels or ()):
        notes = task.notes or ""
        m = DEFECTS.search(notes)
        if m:
            parts += ["## The auditor's measured defect list (this is a fidelity FIX)", "", "```", notes[m.start():][:4000], "```", ""]
        else:
            parts += ["## Fidelity", "", "This task is labelled `fidelity` but carries no `DEFECTS:`/`MEASURED:` note — run the audit first, or ask for the list.", ""]
    if extra.strip():
        parts += ["## From the orchestrator", "", extra.strip(), ""]
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    import time

    ap = argparse.ArgumentParser(prog="worker-prompt.sh", description="Assemble a writer's prompt from the task record, the epic's SPEC INDEX slice, the relevant memory keys and (for a fidelity task) the defect list. What the orchestrator adds goes in --extra.")
    ap.add_argument("task")
    ap.add_argument("--lane", default=None)
    ap.add_argument("--worker", type=int, default=None)
    ap.add_argument("--extra", default=None, help="a file whose text is appended under 'From the orchestrator'")
    ap.add_argument("--out", default=None, help="write here (default: .harness/run/prompts/<task>-<HHMMSS>.md) and print the path")
    args = ap.parse_args(argv)
    extra = ""
    if args.extra:
        try:
            extra = Path(args.extra).read_text()
        except OSError as exc:
            print(f"cannot read --extra {args.extra}: {exc}", file=sys.stderr)
            return 2
    try:
        text = build(args.task, lane=args.lane, worker=args.worker, extra=extra)
    except LookupError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else REPO / ".harness" / "run" / "prompts" / f"{args.task.replace('/', '-')}-{time.strftime('%H%M%S')}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
