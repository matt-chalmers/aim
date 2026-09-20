"""An epic's staged files, and which spelling of its id names them.

Two scripts in one directory required OPPOSITE id forms and each failed plausibly on the
other's. `spec-index-status.sh` globbed the staging folder with the id as given, and the
folders are named `<bare-id>-<slug>` — so the prefixed id every other command takes
(`PROJ-m7j7`) matched nothing and printed `REBUILD — no spec index`, a legitimate-looking
verdict that cost a ~120k-token survey for an index already on disk. `render-epic.sh` did
the reverse: the bare id resolved no children and rendered "The epic has not been planned
yet" for an epic with 25. Anyone who learned the workaround for one applied it to the other
and was silently wrong in the other direction.

One rule now: every consumer accepts EITHER form, and a lookup that matches nothing says so
and names what it tried.
"""

from __future__ import annotations

import datetime as _dt
import re as _re
from pathlib import Path

from .port import TaskStore


def known_prefix() -> str | None:
    """The project's declared id prefix (`beads.prefix`), or None where none is declared —
    the markdown backend mints `t-xxxx` ids and needs no prefix."""
    try:
        from models.project import load

        return load().bead_prefix()
    except Exception:  # noqa: BLE001 — no config, no prefix: the id-derived forms still apply
        return None


def id_forms(epic: str, prefix: str | None = None) -> tuple[str, ...]:
    """Every spelling a staged folder or a record might carry for `epic`, as given first.

    With a known prefix: the bare form (prefix stripped) or the prefixed form (prefix
    added), whichever the given one is not. Without one: the text after the first dash,
    which is what a prefixed id reduces to — `PROJ-m7j7` → `m7j7` — and an id with no
    dash has only itself.
    """
    forms = [epic]
    if prefix and epic.startswith(f"{prefix}-"):
        forms.append(epic[len(prefix) + 1 :])
    elif prefix:
        forms.append(f"{prefix}-{epic}")
    elif "-" in epic:
        forms.append(epic.split("-", 1)[1])
    return tuple(dict.fromkeys(f for f in forms if f))


def staged_folder(epic: str, proposed: Path, prefix: str | None = None) -> tuple[Path | None, list[str]]:
    """The epic's folder under `proposed` — `<form>` or `<form>-<slug>` — for any id form.

    Returns the folder and the patterns tried, so a miss can be reported with what was
    looked for rather than as an absence.
    """
    prefix = prefix if prefix is not None else known_prefix()
    tried: list[str] = []
    for form in id_forms(epic, prefix):
        pattern = f"{form}*"
        tried.append(str(proposed / pattern))
        hits = sorted(
            p for p in proposed.glob(pattern)
            if p.is_dir() and (p.name == form or p.name.startswith(f"{form}-"))
        )
        if hits:
            return hits[0], tried
    return None, tried


def resolve_epic(store: TaskStore, epic: str) -> str | None:
    """The tracker's own id for `epic`, given either form. Exact match first; otherwise
    the one epic whose id ends in `-<given>`. Two candidates is ambiguity, not a match."""
    try:
        if store.show(epic) is not None:
            return epic
    except Exception:  # noqa: BLE001 — an unknown id is a None, whatever the backend raises
        pass
    suffix = f"-{epic}"
    candidates = [t.id for t in store.list(type="epic") if t.id.endswith(suffix)]
    return candidates[0] if len(candidates) == 1 else None


# --- the writes: what §3 stages, and the next decision-record number ----------------
#
# Until 0.10.26 every one of these was the orchestrator's by hand: compute the folder name
# (and get it wrong in the direction `close_epic._staging` later refuses), copy the
# template, paste the agent's result, allocate an ADR number with `ls` + max + 1 (a
# recorded collision: "two streams picking independently"). The reads above resolved the
# folder; nothing wrote into it.

ADR_NAME = _re.compile(r"^(?P<n>\d{4})-")


def slug_for(title: str, limit: int = 40) -> str:
    """`Add rate limiting to login` → `add-rate-limiting-to-login`."""
    s = _re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return s[:limit].rstrip("-") or "epic"


def bare_id(epic: str, prefix: str | None = None) -> str:
    """The folder-naming form: the id without its prefix, which is how folders are named."""
    prefix = prefix if prefix is not None else known_prefix()
    if prefix and epic.startswith(prefix + "-"):
        return epic[len(prefix) + 1:]
    return epic


def ensure_folder(epic: str, proposed: Path, title: str = "", prefix: str | None = None) -> Path:
    """The epic's staging folder, found or created as `<bare-id>-<slug>`."""
    folder, _ = staged_folder(epic, proposed, prefix)
    if folder is not None:
        return folder
    folder = proposed / f"{bare_id(epic, prefix)}-{slug_for(title)}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def template(proposed: Path, kind: str) -> str | None:
    """`<proposed>/_template-<kind>.md`, or None — a missing template is said by the caller,
    never silently replaced with an invented shape."""
    p = proposed / f"_template-{kind}.md"
    return p.read_text() if p.is_file() else None


def _head_sha(cwd: Path) -> str:
    import subprocess

    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(cwd), capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else "0000000"


def stage_spec_index(folder: Path, epic: str, *, verdict: str, body: str, cites: list[str], cwd: Path, title: str = "") -> Path:
    """`spec-index.md` with the frontmatter `spec-index-status.sh` reads — `generated_sha`
    (the diff baseline), `generated_at`, `verdict`, `cites` — and the survey's SPEC INDEX
    as the body. Pointers only: the caller hands over the survey's index section, never
    spec prose."""
    lines = ["---", f"epic: {epic}", f"generated_at: {_dt.date.today().isoformat()}", f"generated_sha: {_head_sha(cwd)}", f"verdict: {verdict}", "cites:"]
    lines += [f"  - {c}" for c in cites] if cites else ["  []"]
    lines += ["---", "", f"# Spec index — {epic} {title}".rstrip(), "",
              "> **A regenerated cache, not a source.** `analyst-survey` rebuilt it; it tells you where",
              "> to look, the doc tells you what is true. Never read a requirement from this file.",
              "", body.strip(), ""]
    path = folder / "spec-index.md"
    path.write_text("\n".join(lines))
    return path


def cites_in(text: str, cwd: Path) -> list[str]:
    """Every project-relative path the text names that exists — the survey's `cites`."""
    seen: list[str] = []
    for m in _re.finditer(r"(?<![\w./-])((?:[\w.-]+/)+[\w.-]+\.(?:md|py|ts|tsx|js|yaml|yml|json))(?![\w/])", text or ""):
        p = m.group(1).strip("`'\",.;:()")
        if p not in seen and (cwd / p).is_file():
            seen.append(p)
    return seen


def stage_design(folder: Path, epic: str, text: str, *, title: str = "", proposed: Path | None = None) -> Path:
    """`design.md` — the architect's output, under the template's header when the output
    does not already carry a `# Design` title. Status `draft`; fold-in ② at §5 routes it."""
    path = folder / "design.md"
    body = text.strip()
    if not body.lstrip().startswith("# Design"):
        head = [f"# Design: {title or epic}", "", f"> **Epic**: {epic} — {title}".rstrip(" —"), "> **Status**: draft",
                "> **Folds in at epic close** — the *why* to a decision record, the *mechanism* to an architecture doc,",
                "> any contract change to the owning feature doc. Then this file is **deleted**.", ""]
        body = "\n".join(head) + "\n" + body
    path.write_text(body + "\n")
    return path


def draft_adr(folder: Path, epic: str, question: str, task_id: str, *, proposed: Path | None = None) -> Path:
    """A draft decision record beside the design, one per `decision` task the architect
    raised. No number until the owner decides; nothing may cite it as settled."""
    n = 1 + len(list(folder.glob("adr-draft-*.md")))
    path = folder / f"adr-draft-{n}-{slug_for(question, 30)}.md"
    tmpl = template(proposed, "adr-draft") if proposed else None
    if tmpl:
        body = tmpl.replace("{the decision, as a question or a statement}", question)
        body = _re.sub(r"\{[^}]*xxxx\}", epic, body, count=1)
        body = body.replace("{TipDonkey-xxxx} — the `DECISION:` bead this resolves", f"{task_id} — the `DECISION:` task this resolves")
    else:
        body = "\n".join([f"# ADR-XXXX: {question}", "", "**Status**: Proposed — awaiting owner decision", f"**Epic**: {epic}", f"**Task**: {task_id}", "",
                          "> **Draft** — staged; not an ADR yet, and nothing may cite it as settled.", "", "## Context", "", "## Options considered", "", "## Decision", "", "(open)", ""])
    path.write_text(body if body.endswith("\n") else body + "\n")
    return path


def adr_next(adrs: Path) -> int:
    """The next free decision-record number: max of `NNNN-*.md` in `paths.adrs`, plus one.
    Allocated ONCE, by the sequencer, before the planner runs — never by a worker, and
    never by two streams independently (the recorded collision)."""
    n = 0
    if adrs.is_dir():
        for p in adrs.glob("*.md"):
            m = ADR_NAME.match(p.name)
            if m:
                n = max(n, int(m.group("n")))
    return n + 1


HEADING = _re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*$", _re.M)


def section(text: str, heading: str) -> str | None:
    """The body under `## <heading>` (any level), LINE-ANCHORED, up to the next heading of
    the same or higher level — or None when there is no such heading. `spec-editor` was
    told to anchor the match to `^` because an unanchored search once matched the words
    inside a paragraph and folded in nothing, silently; and to stop on a split heading.
    Two headings with the same title is an error, not a choice."""
    hits = [m for m in HEADING.finditer(text or "") if m.group("title").strip().lower() == heading.strip().lower()]
    if not hits:
        return None
    if len(hits) > 1:
        raise ValueError(f"heading {heading!r} appears {len(hits)} times — split headings; fix the document")
    m = hits[0]
    level = len(m.group("hashes"))
    start = m.end()
    end = len(text)
    for n in HEADING.finditer(text, start):
        if len(n.group("hashes")) <= level:
            end = n.start()
            break
    return text[start:end].strip("\n")


FRONT = _re.compile(r"^---\n(?P<fm>.*?)\n---\n", _re.S)


def set_status(path: Path, status: str, **also: str) -> None:
    """Flip `status:` in a staged file's frontmatter (and set any other keys given, e.g.
    `folded_in: <date>`), in place. A file with no frontmatter gets one."""
    text = path.read_text()
    m = FRONT.match(text)
    fields = {"status": status, **also}
    if m:
        body = m.group("fm")
        for k, v in fields.items():
            if _re.search(rf"^{k}:", body, _re.M):
                body = _re.sub(rf"^{k}:.*$", f"{k}: {v}", body, count=1, flags=_re.M)
            else:
                body += f"\n{k}: {v}"
        text = f"---\n{body}\n---\n" + text[m.end():]
    else:
        text = "---\n" + "\n".join(f"{k}: {v}" for k, v in fields.items()) + "\n---\n" + text
    path.write_text(text)


def promote_adr(draft: Path, adrs: Path, *, decision: str, cwd: Path) -> Path:
    """A resolved draft decision record MOVES into `paths.adrs` at the next free number
    (`git mv` — a tracked file keeps its history; an untracked one is renamed), with
    `Status: Accepted` and `## Decision` filled in from the owner's settlement. It moves
    rather than merging because a decision record is a standalone append-only file."""
    import subprocess

    n = adr_next(adrs)
    stem = _re.sub(r"^adr-draft-\d+-", "", draft.stem)
    dest = adrs / f"{n:04d}-{stem}.md"
    adrs.mkdir(parents=True, exist_ok=True)
    moved = subprocess.run(["git", "mv", str(draft), str(dest)], cwd=str(cwd), capture_output=True, text=True)
    if moved.returncode != 0:
        draft.rename(dest)
    text = dest.read_text()
    text = _re.sub(r"^# ADR-XXXX:", f"# ADR-{n:04d}:", text, count=1, flags=_re.M)
    text = _re.sub(r"^\*\*Status\*\*:.*$", "**Status**: Accepted", text, count=1, flags=_re.M)
    text = _re.sub(r"^> \*\*Draft\*\*.*?\n(?:>.*\n)*\n?", "", text, count=1, flags=_re.M)
    if _re.search(r"^## Decision[ \t]*$", text, _re.M):
        text = _re.sub(r"(^## Decision[ \t]*\n)(.*?)(?=^## |\Z)", lambda m: m.group(1) + "\n" + decision.strip() + "\n\n", text, count=1, flags=_re.M | _re.S)
    else:
        text = text.rstrip("\n") + f"\n\n## Decision\n\n{decision.strip()}\n"
    dest.write_text(text)
    return dest
