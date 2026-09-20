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

    <briefs>/<task>-<sha8>/brief.md            measurements only. NO diff body, NO diff
                                               pointer. The one file every lens reads.
    <briefs-diff>/<task>-<sha8>/artefacts.md   where the patches are and how to read them
    <briefs-diff>/<task>-<sha8>/stat.txt       the --stat table
    <briefs-diff>/<task>-<sha8>/files.txt      changed paths, one per line
    <briefs-diff>/<task>-<sha8>/full.patch     the whole diff, on disk, referenced by path
    <briefs-diff>/<task>-<sha8>/by-file/*      per-file patches, for targeted reads

THE DIFF IS A SIBLING ROOT, NOT A SUBDIRECTORY, AND THAT IS WHAT MAKES THE RULE
PHYSICAL. Until 0.10.21 the diff sat under `<brief root>/diff/`, `brief.md` printed those
paths in a section headed "read only what you need", and every reader was granted the
briefs root — so "L3 must not read diff/" was a request in the very file that told it
where the diff was. `docs/concepts/verification.md` called the separation "physical, not
instructional"; it was the reverse. Now `brief.md` carries no pointer, the diff lives
under `briefs-diff/` beside the briefs, and `resolve.py` DENIES `verifier-spec` that root
(`Read(//…/briefs-diff/**)` — deny beats allow, and it covers `cat`/`head`/`sed` too).
L1, L2 and L4 are handed `artefacts.md` by path. That the brief carries no diff body is
asserted by ``test_brief_carries_no_diff_body``; that it carries no diff pointer, by
``test_brief_names_no_diff_path``.

THE L4 TRIGGER IS COMPUTED HERE, NOT BY THE ORCHESTRATOR. `/swarm` step 7 said "compute
the trigger mechanically, from `git diff --name-only` plus a grep of the diff body, plus
the task's `SURFACE:` line — never from the worker's summary", and then had the
orchestrator do it, at its context price, with this module already holding all three
inputs and computing the first. The incident that shaped the rule: L4 once fired only
because the orchestrator hand-reasoned that a derived timestamp *was* the access control,
and found a hole letting any authenticated user alter another account's records. That
should never have rested on a judgement call; `l4_trigger` is the rule as code, with
"doubt fires" as its default.
"""

from __future__ import annotations

import json
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
#: The diff artefacts, BESIDE the briefs, never under them: the root `verifier-spec` is
#: denied. `resolve.diff_root()` must agree with this; a test pins that they do.
DIFF_ROOT = REPO / ".harness" / "run" / "briefs-diff"

#: Words on a task's `SURFACE:` line that name a security surface. The line is the
#: planner's answer to "what does this task touch"; "none of the declared invariants"
#: with none of these is the one spelling that does NOT fire L4.
SURFACE_WORDS = re.compile(
    r"authori[sz]|permission|access|tenant|isolation|exposure|integrity|published|privacy|secret|session|csrf|injection|token|credential",
    re.IGNORECASE,
)
SURFACE_LINE = re.compile(r"^\s*SURFACE:\s*(?P<text>.*)$", re.IGNORECASE | re.MULTILINE)
#: The marker a worker leaves at a core change; three lenses grep for it separately.
CORE_CHANGE = re.compile(r"CORE-CHANGE\(([^)]*)\)")

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
    #: Where the diff artefacts go. NEVER under `root`; see the module docstring.
    diff_root: Path | None = None
    #: The task's acceptance criteria, verbatim — what L1 locates in the diff.
    acceptance: str = ""
    #: Whether L4 must fire, and why — computed by `l4_trigger`.
    l4: "L4 | None" = None
    #: The one-clean-commit facts L1 used to derive by hand.
    hygiene: "Hygiene | None" = None

    @property
    def diff_dir(self) -> Path:
        return self.diff_root if self.diff_root is not None else DIFF_ROOT / self.root.name

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


@dataclass(frozen=True)
class L4:
    """Whether the security lens must run, and every reason it must."""

    fires: bool
    why: tuple[str, ...] = ()
    surface: str | None = None
    touched_security_path: bool = False

    def as_dict(self) -> dict:
        return {"fires": self.fires, "why": list(self.why), "surface": self.surface, "touched_security_path": self.touched_security_path}


@dataclass(frozen=True)
class Hygiene:
    """One task, one clean commit — the four facts, measured."""

    commits_ahead: int | None  # None: the commit is on the main branch (a /grind close)
    main: str
    names_task: bool
    export_path: str | None
    touches_export: bool
    core_changes: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "commits_ahead": self.commits_ahead, "main": self.main, "names_task": self.names_task,
            "export_path": self.export_path, "touches_export": self.touches_export, "core_changes": list(self.core_changes),
        }


def added_lines(patch: str) -> list[str]:
    """The lines a diff ADDS — `+` but not the `+++` file header. A token grep over the
    whole patch would fire on a secret the change REMOVED."""
    return [ln[1:] for ln in patch.splitlines() if ln.startswith("+") and not ln.startswith("+++")]


def l4_trigger(files: list[str], patch: str, bead_text: str, project: Project | None = None) -> L4:
    """The security lens's trigger, from the three inputs `/swarm` step 7 names — and a
    fourth: no `SURFACE:` line at all. The planner's contract puts one on every task; a
    task without one is a task nobody asked the question of, and doubt fires."""
    p = project or load()
    why: list[str] = []
    touched = False
    for area in p.areas:
        if any(t.lower().startswith("security") or t.lower() == "l4" for t in area.triggers):
            hit = [f for f in files if f.startswith(area.path)]
            if hit:
                why.append(f"area `{area.label}` fires {', '.join(area.triggers)}: {', '.join(hit[:4])}")
    for prefix in p.security_paths():
        hit = [f for f in files if f.startswith(prefix)]
        if hit:
            touched = True
            why.append(f"security.paths `{prefix}`: {', '.join(hit[:4])}")
    added = "\n".join(added_lines(patch)).lower()
    for token in p.security_tokens():
        if token.lower() in added:
            why.append(f"security.tokens `{token}` in an added line")
    m = SURFACE_LINE.search(bead_text or "")
    surface = m.group("text").strip() if m else None
    if surface is None:
        why.append("no SURFACE: line on the task — the planner's contract puts one on every task; doubt fires L4")
    elif SURFACE_WORDS.search(surface):
        why.append(f"SURFACE: {surface[:120]}")
    elif "none" not in surface.lower():
        why.append(f"SURFACE: names something and not 'none' — {surface[:120]}")
    return L4(fires=bool(why), why=tuple(why), surface=surface, touched_security_path=touched)


def hygiene(task: str, sha: str, subject: str, files: list[str], patch: str, main: str, ahead: int | None) -> Hygiene:
    export = None
    try:
        import tracker

        export = tracker.task_store().capabilities().export_path
    except Exception:  # noqa: BLE001 — no tracker, no export to worry about
        export = None
    touches = bool(export) and any(f == export or f.startswith(export.rstrip("/") + "/") for f in files)
    names = re.search(rf"(?<![\w.]){re.escape(task)}(?![\w])", subject) is not None
    return Hygiene(
        commits_ahead=ahead, main=main, names_task=names, export_path=export, touches_export=touches,
        core_changes=tuple(sorted(set(CORE_CHANGE.findall("\n".join(added_lines(patch)))))),
    )


def _ahead_of_main(sha: str) -> tuple[str, int | None]:
    """(main branch, commits `sha` is ahead of it) — None when `sha` is already on main."""
    from models.resume import main_branch

    main = main_branch(CHECKOUT)
    on_main = subprocess.run(["git", "merge-base", "--is-ancestor", sha, main], cwd=str(CHECKOUT), capture_output=True)
    if on_main.returncode == 0:
        return main, None
    count = subprocess.run(["git", "rev-list", "--count", f"{main}..{sha}"], cwd=str(CHECKOUT), capture_output=True, text=True)
    return main, (int(count.stdout.strip() or 0) if count.returncode == 0 else None)


def _git(*args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(CHECKOUT), capture_output=True, text=True)
    if proc.returncode != 0:
        raise BriefError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:200]}")
    return proc.stdout


def _bead_text(task: str) -> tuple[str, list[str], str]:
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
        return "", [f"tracker unavailable ({exc.__class__.__name__}) — task text not included"], ""
    if task is None:
        return "", [f"no task {task!r} in the tracker — text not included"], ""
    lines = [
        f"{task.id}  [{task.type}/{task.status}]  {task.title}",
    ]
    if task.depends_on:
        lines.append(f"depends on: {', '.join(task.depends_on)}")
    if task.description:
        lines += ["", task.description.strip()]
    # THE ACCEPTANCE CRITERIA, which every lens whose job is "locate every criterion in
    # the diff" re-ran `tk.sh show` to get, because the brief built to save that call
    # omitted them.
    if task.acceptance.strip():
        lines += ["", "## Acceptance criteria", task.acceptance.strip()]
    if task.notes:
        lines += ["", "## Notes", task.notes.strip()]
    return "\n".join(lines), [], task.acceptance.strip()


def _slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", path).strip("-")[:120]


def build(task: str, commit: str, root: Path | None = None, diff_root: Path | None = None) -> Brief:
    """Collect the facts and write the diff artefacts to disk — under a SIBLING of the
    brief root, never inside it (`--out X` puts them at `X-diff`)."""
    sha = _git("rev-parse", "--verify", f"{commit}^{{commit}}").strip()
    subject = _git("show", "-s", "--format=%s", sha).strip()
    author = _git("show", "-s", "--format=%an", sha).strip()
    date = _git("show", "-s", "--format=%ad", "--date=short", sha).strip()
    files = [f for f in _git("show", "--name-only", "--format=", sha).splitlines() if f]
    stat = _git("show", "--stat", "--format=", sha).strip()

    ins = sum(int(m) for m in re.findall(r"(\d+) insertion", stat))
    dele = sum(int(m) for m in re.findall(r"(\d+) deletion", stat))

    bead_text, notes, acceptance = _bead_text(task)
    patch = _git("show", "--format=", sha)
    main, ahead = _ahead_of_main(sha)

    if root is None:
        root = (DEFAULT_ROOT / f"{task}-{sha[:8]}").resolve()
        diff_dir = (diff_root or DIFF_ROOT / f"{task}-{sha[:8]}").resolve()
    else:
        root = root.resolve()
        diff_dir = (diff_root or root.parent / f"{root.name}-diff").resolve()
    assert not str(diff_dir).startswith(str(root) + "/"), "the diff must never live under the brief root"

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
        diff_root=diff_dir,
        acceptance=acceptance,
    )
    brief.l4 = l4_trigger(files, patch, bead_text, brief.project)
    brief.hygiene = hygiene(task, sha, subject, files, patch, main, ahead)

    d = brief.diff_dir
    (d / "by-file").mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    (d / "stat.txt").write_text(stat + "\n")
    (d / "files.txt").write_text("\n".join(files) + "\n")
    (d / "full.patch").write_text(patch)
    for f in files:
        try:
            (d / "by-file" / f"{_slug(f)}.patch").write_text(
                _git("show", "--format=", sha, "--", f)
            )
        except BriefError:
            continue  # a path git can no longer address is not worth failing the brief
    (d / "artefacts.md").write_text(render_artefacts(brief))
    (root / "brief.md").write_text(render(brief))
    (root / "l4.json").write_text(json.dumps(brief.l4.as_dict(), indent=2) + "\n")
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

    if b.l4 is not None:
        lines += ["## L4 trigger (mechanical)", ""]
        if b.l4.fires:
            lines += [f"**`verifier-security` must run.** {len(b.l4.why)} reason(s):", ""] + [f"- {w}" for w in b.l4.why] + [""]
        else:
            lines += ["`verifier-security` is not required by any declared surface, token or the task's `SURFACE:` line. ",
                      f"SURFACE: {b.l4.surface}", ""]
    if b.hygiene is not None:
        h = b.hygiene
        ahead = "on the main branch" if h.commits_ahead is None else f"{h.commits_ahead} commit(s) ahead of `{h.main}`"
        lines += [
            "## Commit hygiene (mechanical)", "",
            f"- {ahead}" + ("" if h.commits_ahead in (None, 1) else " — **one task is one commit**"),
            f"- subject names the task id: {'yes' if h.names_task else '**NO**'}",
            f"- touches the tracker export ({h.export_path or 'none declared'}): {'**YES — never committed by a worker**' if h.touches_export else 'no'}",
            f"- CORE-CHANGE markers added: {', '.join(h.core_changes) if h.core_changes else 'none'}",
            "",
        ]
    lines += [
        "## The diff",
        "",
        "This brief carries **no diff body and no pointer to one**. The lenses that judge the",
        "change itself (L1, L2, L4) are handed an `artefacts.md` by path, beside this brief;",
        "`verifier-spec` (L3) must not see the diff, is denied its directory, and reasons from",
        "the task and the repository as it now stands — that independence is what makes its",
        "findings worth having alongside L1's. Never `git show <sha>`: ~110k tokens on a change",
        "this size.",
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


def render_artefacts(b: Brief) -> str:
    """The diff pointers — for L1, L2 and L4 ONLY, handed by path, never in brief.md."""
    d = b.diff_dir
    return "\n".join([
        f"# Diff artefacts — {b.task} @ {b.commit[:12]}",
        "",
        "On disk — read only what you need. The full diff is ~110k tokens on a change this",
        "size. Do not `git show` it. Read the per-file patch for the files your lens actually",
        "reasons about; `peek.sh <patch>:START-END` for a slice.",
        "",
        "```",
        f"{d}/stat.txt              the --stat table",
        f"{d}/files.txt             changed paths, one per line",
        f"{d}/by-file/<slug>.patch  ONE file's diff  (slug = path, non-alnum -> '-')",
        f"{d}/full.patch            the whole diff, if you truly need it",
        "```",
        "",
        "Files:",
        "",
    ] + [f"- `{f}` → `by-file/{_slug(f)}.patch`" for f in b.files] + [""])


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
    ap.add_argument("--json", action="store_true", help="print the paths and the L4 trigger as one JSON object on stdout")
    args = ap.parse_args(argv)

    try:
        b = build(args.task, args.commit, args.out)
    except BriefError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    size = (b.root / "brief.md").stat().st_size
    full = (b.diff_dir / "full.patch").stat().st_size
    if args.json:
        print(json.dumps({
            "brief": str(b.root / "brief.md"), "root": str(b.root), "diff_root": str(b.diff_dir),
            "artefacts": str(b.diff_dir / "artefacts.md"), "commit": b.commit, "files": len(b.files),
            "l4": b.l4.as_dict() if b.l4 else None, "hygiene": b.hygiene.as_dict() if b.hygiene else None,
        }))
        return 0
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
