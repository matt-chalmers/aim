"""Precompute the facts every verification lens would otherwise re-derive.

WHY THIS EXISTS, IN NUMBERS. swarm.md's measured cost model is
``tokens ~= 18,700 + 2,600 x tool_calls``, and the two lens dispatches measured
this session were 100% bash — 43 calls / 127k tokens and 39 calls / 110k tokens.
Four lenses run per task, and each one independently derives the same three
things: the diff stat, the changed-file list, and the task text.

The single biggest line item is the diff. Measured on commit 21add61 (44 files):

    git show <sha>          387,929 chars   ~110,800 tokens
    git show --stat <sha>     5,738 chars     ~1,600 tokens

A lens that reads the full diff spends roughly its entire budget on it. So the
brief gives every lens the *stat* and the *paths*, writes the diff to disk
per-file, and lets a lens pull only the files it actually needs to reason about.

LAYERING IS A CORRECTNESS REQUIREMENT, NOT A CONVENIENCE. swarm.md's
decorrelation rule is that L3 (`verifier-spec`) sees the task and the repo at
HEAD and **never the diff** — that is what makes its findings independent of
L1's. So the brief is split:

    brief.md          measurements only. Contains NO diff body. Safe for L3.
    diff/stat.txt     the --stat table
    diff/files.txt    changed paths, one per line
    diff/full.patch   the whole diff, on disk, referenced by path
    diff/by-file/*    per-file patches, for targeted reads

`brief.md` is the only file handed to every lens. Anything under `diff/` is
handed to L1, L2 and L4 by path, and withheld from L3. That the brief carries no
diff body is asserted by ``test_brief_carries_no_diff_body`` rather than left to
whoever edits this next.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from models.project import (
    Project,
    load,
)  # sibling top-level package; harness/ is the root
from models.resolve import CHECKOUT, REPO

#: Where a task's brief is written unless the caller says otherwise: INSIDE THE PROJECT,
#: under its gitignored run directory, which every lens can read because it is the lens's
#: own working directory. Briefs used to follow SCRATCHPAD/TMPDIR, and a lens was handed
#: that directory — but the two sides computed it in different environments once the
#: dispatcher ran outside the orchestrator's sandbox (the sandbox points TMPDIR at its own
#: tmp), and every lens in a headless epic was denied `Read` on its own brief. Measured:
#: six denials, six operator questions filed, one task close blocked by them.
DEFAULT_ROOT = REPO / ".harness" / "run" / "briefs"

#: The area map now lives in `harness.yaml`, not here. Grouping changed
#: paths is a fact about *this repository's layout*, and the same map drives which
#: verification lens a change must fire — so it belongs in one declared place
#: rather than duplicated between a Python constant and a prose trigger list.


class BriefError(RuntimeError):
    """The brief could not be built, and a lens must not be dispatched without one."""


@dataclass
class Brief:
    task: str
    commit: str
    subject: str
    author: str
    date: str
    files: list[str]
    stat: str
    bead_text: str
    root: Path
    insertions: int = 0
    deletions: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def diff_dir(self) -> Path:
        return self.root / "diff"

    project: Project | None = None

    def grouped(self) -> list[tuple[str, str, list[str], tuple[str, ...]]]:
        """Changed paths bucketed by area, plus an `other` bucket. No path is dropped.

        A path matching no declared area lands in `other` rather than vanishing —
        a dropped path sends the lens back to `git show`, costing more than no
        brief at all.
        """
        areas = (self.project or load()).areas
        seen: set[str] = set()
        out: list[tuple[str, str, list[str], tuple[str, ...]]] = []
        for area in areas:
            hit = [f for f in self.files if f.startswith(area.path) and f not in seen]
            if hit:
                seen.update(hit)
                out.append((area.path, area.label, sorted(hit), area.triggers))
        rest = sorted(f for f in self.files if f not in seen)
        if rest:
            out.append(("", "other", rest, ()))
        return out


def _git(*args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(CHECKOUT), capture_output=True, text=True)
    if proc.returncode != 0:
        raise BriefError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:200]}")
    return proc.stdout


def _bead_text(task: str) -> tuple[str, list[str]]:
    """The task's own text, or a clear note that it was unavailable.

    A missing record is NOT fatal — the diff half of the brief is still worth having,
    and a lens told plainly that the text is absent behaves better than one handed a
    silently empty section.

    Read through the tracker port rather than shelling out, so a lens brief is the same
    whichever backend the project declares.
    """
    try:
        import tracker

        task = tracker.task_store().show(task)
    except Exception as exc:  # noqa: BLE001 — a tracker outage must not fail the brief
        return "", [f"tracker unavailable ({exc.__class__.__name__}) — task text not included"]
    if task is None:
        return "", [f"no task {task!r} in the tracker — text not included"]
    lines = [
        f"{task.id}  [{task.type}/{task.status}]  {task.title}",
    ]
    if task.depends_on:
        lines.append(f"depends on: {', '.join(task.depends_on)}")
    if task.description:
        lines += ["", task.description.strip()]
    if task.notes:
        lines += ["", "## Notes", task.notes.strip()]
    return "\n".join(lines), []


def _slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", path).strip("-")[:120]


def build(task: str, commit: str, root: Path | None = None) -> Brief:
    """Collect the facts and write the diff artefacts to disk."""
    sha = _git("rev-parse", "--verify", f"{commit}^{{commit}}").strip()
    subject = _git("show", "-s", "--format=%s", sha).strip()
    author = _git("show", "-s", "--format=%an", sha).strip()
    date = _git("show", "-s", "--format=%ad", "--date=short", sha).strip()
    files = [f for f in _git("show", "--name-only", "--format=", sha).splitlines() if f]
    stat = _git("show", "--stat", "--format=", sha).strip()

    ins = sum(int(m) for m in re.findall(r"(\d+) insertion", stat))
    dele = sum(int(m) for m in re.findall(r"(\d+) deletion", stat))

    bead_text, notes = _bead_text(task)

    root = (root or DEFAULT_ROOT / f"{task}-{sha[:8]}").resolve()
    brief = Brief(
        task=task,
        commit=sha,
        subject=subject,
        author=author,
        date=date,
        files=files,
        stat=stat,
        bead_text=bead_text,
        root=root,
        insertions=ins,
        deletions=dele,
        notes=notes,
    )

    d = brief.diff_dir
    (d / "by-file").mkdir(parents=True, exist_ok=True)
    (d / "stat.txt").write_text(stat + "\n")
    (d / "files.txt").write_text("\n".join(files) + "\n")
    (d / "full.patch").write_text(_git("show", "--format=", sha))
    for f in files:
        try:
            (d / "by-file" / f"{_slug(f)}.patch").write_text(
                _git("show", "--format=", sha, "--", f)
            )
        except BriefError:
            continue  # a path git can no longer address is not worth failing the brief
    (root / "brief.md").write_text(render(brief))
    return brief


def render(b: Brief) -> str:
    """The shared brief. MEASUREMENTS ONLY — no diff body, so L3 can read it."""
    lines = [
        f"# Lens brief — {b.task}",
        "",
        "Precomputed so each lens does not re-derive it. Everything here is a",
        "**measurement**, never a judgement, and this file contains **no diff body** —",
        "it is therefore safe to hand to `verifier-spec` (L3), which must not see the diff.",
        "",
        "## Change under review",
        "",
        f"- commit: `{b.commit}`",
        f"- subject: {b.subject}",
        f"- author: {b.author} · {b.date}",
        f"- scope: **{len(b.files)} files**, +{b.insertions} / -{b.deletions}",
        "",
        "## Changed paths by area",
        "",
    ]
    fired: set[str] = set()
    for prefix, label, hit, triggers in b.grouped():
        head = f"**{label}** ({len(hit)})" + (f" — `{prefix}`" if prefix else "")
        if triggers:
            fired.update(triggers)
            head += f"  ⚠ **fires: {', '.join(triggers)}**"
        lines.append(head)
        lines += [f"- `{f}`" for f in hit] + [""]

    if fired:
        lines += [
            "## Lenses this change must fire",
            "",
            f"**{', '.join(sorted(fired))}** — from the area map in `harness.yaml`.",
            "",
            "This is the path-based answer only. A task's `SURFACE:` line can fire a lens the",
            "paths do not, and a zero path grep is never an exemption.",
            "",
        ]

    lines += [
        "## Diff artefacts (on disk — read only what you need)",
        "",
        "The full diff is ~110k tokens on a change this size. Do not `git show` it.",
        "Read the per-file patch for the files your lens actually reasons about:",
        "",
        "```",
        f"{b.diff_dir}/stat.txt              the --stat table",
        f"{b.diff_dir}/files.txt             changed paths, one per line",
        f"{b.diff_dir}/by-file/<slug>.patch  ONE file's diff  (slug = path, non-alnum -> '-')",
        f"{b.diff_dir}/full.patch            the whole diff, if you truly need it",
        "```",
        "",
        "**L3 (`verifier-spec`) must not read anything under `diff/`** — it reasons from",
        "the task and the repo at HEAD, and that independence is what makes its findings",
        "worth having alongside L1's.",
        "",
        "## Diff stat",
        "",
        "```",
        b.stat,
        "```",
        "",
    ]

    if b.bead_text:
        lines += ["## Task", "", "```", b.bead_text, "```", ""]
    if b.notes:
        lines += ["## Brief generation notes", ""] + [f"- {n}" for n in b.notes] + [""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(
        prog="brief.sh",
        description="Precompute the shared lens brief for one task and commit.",
    )
    ap.add_argument("task")
    ap.add_argument("commit", nargs="?", default="HEAD")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    try:
        b = build(args.task, args.commit, args.out)
    except BriefError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    size = (b.root / "brief.md").stat().st_size
    full = (b.diff_dir / "full.patch").stat().st_size
    print(b.root / "brief.md")
    print(
        f"-- {len(b.files)} files, +{b.insertions}/-{b.deletions} · "
        f"brief {size:,}B (~{size // 4:,} tok) vs full diff {full:,}B (~{full // 4:,} tok)",
        file=sys.stderr,
    )
    for n in b.notes:
        print(f"-- note: {n}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
