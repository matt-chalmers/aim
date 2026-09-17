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
