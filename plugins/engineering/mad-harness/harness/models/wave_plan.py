"""Compose one wave — `/swarm` steps 2 and 3 as one call, with the judgement handed back.

WHAT THE ORCHESTRATOR DID BY HAND. `tk.sh ready` (with a `--label` flag that did not exist
until 0.10.19), take the top n by priority, clamp to the lane cap in `harness.yaml` and the
per-class table, ask `resume-point.sh` for every candidate, pull the paths each task names,
intersect them pairwise and drop the lower-priority task of any pair that shares one,
`wc -l` every path against `signals.megafile_lines` and make any megafile a lane of width
one, and `git merge-tree` the branches that exist. Six to ten calls at the orchestrator's
context price, and the recorded miss is the one the dependency graph physically cannot
see: `tk.sh validate` rated an epic 11-wide when five of its six wave-1 tasks touched one
very large shared file.

WHAT STAYS WITH THE ORCHESTRATOR. Two checks need judgement and the script prints their
inputs side by side: the SHARED-VOCABULARY check (two tasks that would each *introduce*
the same new field, error code, decision-record number or schema — one must consume it)
and the NEW-FILE check (two tasks that would each *create* the same new file collide
invisibly — a test-data factory module is the usual suspect). The script lists each
candidate's title, first description line, and the paths it names that do not yet
exist; the orchestrator names the collision, if any, and drops one.

NOT ON THE PORT. `graph.validate` levels waves from records alone, shared by both
backends, and `port.Validation` says of its number "it ignores file contention" as a
stated property. Contention needs git and the filesystem, and
`test_port_exposes_no_filesystem_paths` forbids a path on the port; this layers above it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .resolve import REPO

#: A path-looking token in a task's text: has a `/`, or a source-file extension.
PATHISH = re.compile(r"(?<![\w./-])((?:[\w.-]+/)+[\w.-]+|[\w-]+\.(?:py|ts|tsx|js|jsx|go|rb|rs|java|kt|sql|md|yaml|yml|json|toml|css|scss|html))(?![\w/])")
#: Tokens that look like paths and never are.
NOISE = re.compile(r"^(https?:|www\.|\d+/\d+|[A-Z]{2,}-\d+|e\.g\.|i\.e\.|etc\.)|\.\.")


@dataclass
class Candidate:
    id: str
    title: str
    priority: int | None
    created_at: str
    text: str
    labels: tuple[str, ...]
    state: str = "FRESH"
    branch: str | None = None
    existing: list[str] = field(default_factory=list)
    would_create: list[str] = field(default_factory=list)
    dropped: str | None = None

    @property
    def sort_key(self):
        return (self.priority is None, self.priority if self.priority is not None else 0, self.created_at, self.id)


def paths_in(text: str, cwd: Path) -> tuple[list[str], list[str]]:
    """(existing, would-create) paths a task's text names, project-relative, deduplicated."""
    seen: list[str] = []
    for m in PATHISH.finditer(text or ""):
        tok = m.group(1).strip(".,;:()[]`'\"")
        if not tok or NOISE.match(tok) or tok in seen:
            continue
        seen.append(tok)
    existing = [p for p in seen if (cwd / p).exists()]
    creating = [p for p in seen if p not in existing and not p.endswith("/")]
    return existing, creating


def line_count(cwd: Path, path: str) -> int:
    try:
        with open(cwd / path, "rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def merge_tree_conflicts(cwd: Path, a: str, b: str) -> tuple[bool | None, list[str]]:
    """(conflicts?, paths) from `git merge-tree --write-tree`, or (None, []) when git
    cannot say — an old git, or a ref that is not there. None is reported, never
    read as clean."""
    proc = subprocess.run(["git", "merge-tree", "--write-tree", "--name-only", a, b], cwd=str(cwd), capture_output=True, text=True, timeout=120)
    if proc.returncode == 0:
        return False, []
    if proc.returncode == 1:
        lines = [ln.strip() for ln in proc.stdout.splitlines()[1:] if ln.strip()]
        return True, lines
    return None, []


def compose(
    lane: str,
    n: int | None,
    *,
    parent: str | None,
    project,
    cwd: Path,
    ready,
    resume_for,
    env: dict[str, str] | None = None,
) -> dict:
    """Everything the wave needs, as data. `ready` and `resume_for` are injected: the
    tracker's ready set, and the resume point for one task."""
    env = env if env is not None else dict(os.environ)
    notes: list[str] = []
    lanes = project.lanes()
    if lane not in lanes:
        return {"error": f"lane {lane!r} is not declared in harness.yaml (lanes: {', '.join(sorted(lanes)) or 'none'})"}
    rows = ready(parent)
    cands = [Candidate(t.id, t.title, t.priority, t.created_at or "", "\n".join(x for x in (t.description, t.acceptance, t.notes) if x), tuple(t.labels)) for t in rows]
    labelled = [c for c in cands if lane in c.labels]
    if labelled:
        cands = labelled
    elif cands:
        notes.append(f"no ready task carries the label `{lane}` — every ready task considered (labels unused in this epic)")
    if not cands:
        return {"lane": lane, "wave": [], "dropped": [], "merge_only": [], "notes": notes, "why_empty": "nothing ready" + (f" under {parent}" if parent else "")}

    cands.sort(key=lambda c: c.sort_key)
    cap = project.lane_cap(lane)
    global_cap = None
    try:
        global_cap = int(env.get("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS") or 0) or None
    except ValueError:
        global_cap = None
    width = min(x for x in (n, cap, global_cap, len(cands)) if x)
    notes.append(f"width {width} = min(n={n or '-'}, lane cap={cap or '-'}, CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS={global_cap or '-'}, ready={len(cands)})")

    for c in cands:
        rp = resume_for(c.id)
        c.state, c.branch = rp.state, rp.branch
        c.existing, c.would_create = paths_in(c.text, cwd)

    merge_only = [c for c in cands if c.state == "MERGE"]
    pool = [c for c in cands if c.state != "MERGE"]

    # Pairwise shared existing paths: the lower-priority task (later in the order) drops.
    # A shared path past `signals.megafile_lines` is named as the megafile it is: "however
    # disjoint the functions look", one task per wave — the check `tk.sh validate`
    # physically cannot make (an epic rated 11-wide with five tasks on one file).
    threshold = project.megafile_lines()
    if not threshold:
        notes.append("signals.megafile_lines not declared — a shared megafile is reported as a shared path only")
    kept: list[Candidate] = []
    for c in pool:
        for k in kept:
            shared = sorted(set(c.existing) & set(k.existing))
            if shared:
                big = [p for p in shared if threshold and line_count(cwd, p) > threshold]
                if big:
                    c.dropped = f"megafile {big[0]} ({line_count(cwd, big[0])} lines > {threshold}) already owned by {k.id} this wave — width 1"
                else:
                    c.dropped = f"shares {shared[0]} with {k.id}" + (f" (+{len(shared) - 1} more)" if len(shared) > 1 else "")
                break
        if c.dropped is None:
            kept.append(c)

    # Merge-tree between candidates that already have branches.
    with_branch = [c for c in kept if c.branch]
    for i, a in enumerate(with_branch):
        for b in with_branch[i + 1:]:
            if b.dropped:
                continue
            verdict, paths = merge_tree_conflicts(cwd, a.branch, b.branch)
            if verdict is None:
                notes.append(f"git merge-tree could not compare {a.branch} and {b.branch} — pairwise conflicts NOT checked for that pair")
            elif verdict:
                b.dropped = f"branch {b.branch} conflicts with {a.branch}: {', '.join(paths[:3]) or 'see git merge-tree'}"
    kept = [c for c in kept if c.dropped is None]

    wave = kept[:width]
    deferred = kept[width:]
    for c in deferred:
        c.dropped = f"beyond width {width} this wave"

    def row(c: Candidate) -> dict:
        return {"id": c.id, "title": c.title, "priority": c.priority, "state": c.state, "branch": c.branch,
                "existing": c.existing, "would_create": c.would_create, "first_line": (c.text.strip().splitlines() or [""])[0][:120]}

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(cwd), capture_output=True, text=True)
    return {
        "lane": lane, "width": width, "wave_base": head.stdout.strip() if head.returncode == 0 else None,
        "wave": [row(c) for c in wave],
        "dropped": [{"task": c.id, "reason": c.dropped} for c in pool if c.dropped],
        "merge_only": [row(c) for c in merge_only],
        "notes": notes,
    }


def render(plan: dict) -> str:
    if "error" in plan:
        return f"REFUSED: {plan['error']}"
    if plan.get("why_empty"):
        return f"wave {plan['lane']}: {plan['why_empty']}\n" + "\n".join(f"  note: {n}" for n in plan["notes"])
    out = [f"wave {plan['lane']} n={len(plan['wave'])} (width {plan['width']}):"]
    for r in plan["wave"]:
        state = r["state"] + (f" {r['branch']}" if r["branch"] and r["state"] != "FRESH" else "")
        out.append(f"  {r['id']:<18} P{r['priority'] if r['priority'] is not None else '-'}  {state:<32} {r['title'][:60]}")
    if plan["merge_only"]:
        out.append("MERGE, no worker — put straight on step 8's list:")
        out += [f"  {r['id']:<18} {r['branch']}" for r in plan["merge_only"]]
    if plan["dropped"]:
        out.append("dropped this wave:")
        out += [f"  {d['task']:<18} {d['reason']}" for d in plan["dropped"]]
    for n in plan["notes"]:
        out.append(f"  note: {n}")
    out.append("")
    out.append("JUDGEMENT — shared vocabulary: would two of these each INTRODUCE the same new field, error code, decision-record number, component or schema? One must consume it; drop the other.")
    out += [f"  {r['id']:<18} {r['title'][:50]:<50} | {r['first_line'][:80]}" for r in plan["wave"]]
    out.append("JUDGEMENT — new files: would two of these each CREATE the same new file (a test-data factory module is the usual suspect)?")
    out += [f"  {r['id']:<18} creates: {', '.join(r['would_create'][:6]) or '(none named)'}" for r in plan["wave"]]
    if plan.get("manifest"):
        out.append(f"manifest: {plan['manifest']}")
    return "\n".join(out)


def _ready(parent):
    import tracker

    return tracker.task_store().ready(parent=parent)


def _resume(task):
    from .resume import resume_point

    return resume_point(task)


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .project import ProjectError, load

    ap = argparse.ArgumentParser(prog="wave-plan.sh", description="/swarm steps 2-3 as one call: the ready set for a lane, clamped, resume points asked, shared paths and megafiles dropped, branches merge-tree'd; the two judgement checks printed for you.")
    ap.add_argument("lane")
    ap.add_argument("n", nargs="?", type=int, default=None)
    ap.add_argument("--parent", help="scope to this epic's ready children")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-manifest", action="store_true", help="do not open a wave manifest")
    args = ap.parse_args(argv)
    try:
        project = load()
    except ProjectError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    try:
        plan = compose(args.lane, args.n, parent=args.parent, project=project, cwd=REPO, ready=_ready, resume_for=_resume)
    except Exception as exc:  # noqa: BLE001 — a tracker that cannot answer is a stop, named, never an empty wave
        print(f"FAIL: could not read the ready set — {exc.__class__.__name__}: {str(exc)[:200]}", file=sys.stderr)
        return 2
    if "error" in plan:
        print(render(plan), file=sys.stderr)
        return 2
    if plan.get("why_empty"):
        print(render(plan))
        return 1
    if args.parent and not args.no_manifest:
        from . import wave_manifest

        path = wave_manifest.open_wave(args.parent, lane=args.lane, planned=[r["id"] for r in plan["wave"]], dropped=plan["dropped"], wave_base=plan["wave_base"] or "")
        plan["manifest"] = str(path)
        why = wave_manifest.heartbeat(path, "DISPATCH", f"n={len(plan['wave'])} ids={','.join(r['id'] for r in plan['wave'])}")
        if why:
            plan["notes"].append(f"heartbeat not written — {why}")
    print(json.dumps(plan) if args.json else render(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
