"""Where setup stands — which opening applies and which blocks are owed — computed, never
asked, and never stored.

ONE ENGINE, SEVERAL OPENINGS. First run, upgrade, revisit, repair, re-stamp and refuse are
not six flows; they decide only which blocks start owed, and one protocol walks the result.
They compose: an upgrade on a repository that also gained a toolchain owes both.

NOTHING HERE IS A STORED QUEUE. A block with no ledger entry is owed; a confirmed block
whose value changed is owed; a block a newer upgrade note touches is owed until it is
answered at the installed version. Answering a block writes its entry, so recomputing after
an interruption resumes exactly at the block the owner never answered (spec D-22).

WHAT IS NOT DONE HERE: mapping an upgrade note's prose to a block. That is a reading, which
stays with setup's agent (spec D-18); this hands it the notes and each block's `at`.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from typing import Any

import yaml

from . import project as _project
from .discover import dependencies, discover, roots
from .project import ProjectError, _version_key, plugin_version, upgrade_status
from .setup_blocks import BLOCKS
from .setup_ledger import LedgerError, dependency_drift, hash_stale, read
from .setup_notes import sections_after


@dataclass
class State:
    refuse: str | None = None
    render_first_run: bool = False
    openings: list[str] = field(default_factory=list)
    queue: list[dict[str, Any]] = field(default_factory=list)
    stamp_due: bool = False
    notes: list[dict] = field(default_factory=list)
    ledger_at: dict[str, str] = field(default_factory=dict)
    findings: list[dict] = field(default_factory=list)
    hash_stale: list[str] = field(default_factory=list)
    upgrade: str = "current"
    total: int = len(BLOCKS)
    #: Set only when nothing is owed and nothing was asked for: say so and stop.
    nothing_owed: bool = False


#: The blocks a walk presents — the stamp is governed by `stamp_due`, never queued.
WALKED = tuple(b for b in BLOCKS if b.id != "stamp")


def _owe(queue: dict[str, list[str]], block_id: str, reason: str) -> None:
    queue.setdefault(block_id, []).append(reason)


def compute(args: list[str] | None = None) -> State:
    args = list(args or [])
    s = State()
    installed = plugin_version()
    exists = _project.PROJECT_FILE.exists()
    raw: dict = (
        (yaml.safe_load(_project.PROJECT_FILE.read_text()) or {}) if exists else {}
    )
    stamped = (
        str((raw.get("harness") or {}).get("version"))
        if (raw.get("harness") or {}).get("version")
        else None
    )
    s.upgrade = upgrade_status(stamped, installed) if exists else "unstamped"

    # 1. A config written for a newer plugin: the PLUGIN needs updating, not the config.
    if exists and s.upgrade == "ahead":
        s.refuse = (
            f"harness.yaml is stamped {stamped} but the installed plugin is {installed}: the "
            f"plugin is behind, not the config. Update the plugin (`claude plugin update`) "
            f"and run /harness-setup again; nothing was changed."
        )
        return s

    queue: dict[str, list[str]] = {}
    ledger = read()
    entries = ledger.get("blocks") or {}
    s.ledger_at = {k: v.get("at") for k, v in entries.items()}

    # 2. No config at all: render, and owe every block.
    if not exists:
        s.render_first_run = True
        s.openings.append("first-run")
        for b in WALKED:
            _owe(queue, b.id, "first run")
    else:
        # 3. No ledger entry: never reviewed under the ledger — an interrupted pass, or a
        #    config from before the ledger existed.
        unreviewed = [b for b in WALKED if b.id not in entries]
        if unreviewed:
            s.openings.append("unreviewed")
            for b in unreviewed:
                _owe(queue, b.id, "not yet reviewed")
        # A machine's change is recorded so it never WARNS (the pre-flight would otherwise
        # nag about drift it caused), but it is not a person's review: it stays owed until
        # the owner confirms it.
        for b in WALKED:
            if (entries.get(b.id) or {}).get("state") == "repaired":
                _owe(queue, b.id, "changed by the command repair; confirm it")
        # 4. A confirmed block whose value changed since.
        s.hash_stale = hash_stale(raw, ledger)
        for block_id in s.hash_stale:
            _owe(queue, block_id, "your edit, unconfirmed")

    # 5. What discovery found that the config does not say.
    project = None
    if exists:
        try:
            project = _project.load()
        except ProjectError:
            project = None
    found = discover(project)
    s.findings = [asdict(f) | {"key": f.key} for f in found]
    revisit = False
    for f in found:
        if f.declared or f.declined:
            continue
        if f.kind == "stack" and f.state == "present":
            _owe(queue, "stacks", f"undeclared: {f.key} ({', '.join(f.evidence)})")
            revisit = True
        elif f.kind == "framework":
            _owe(queue, "frameworks", f"candidate: {f.name} ({', '.join(f.evidence)})")
            revisit = True
    candidate_roots, _ = roots()
    drift = dependency_drift(
        ledger, {r: d for r in candidate_roots if (d := dependencies(r))}
    )
    for root, names in drift.items():
        _owe(queue, "frameworks", f"new dependencies at {root}: {', '.join(names)}")
        revisit = True
    explicit_revisit = bool(args) and args[0] == "revisit"
    found_anything = bool(queue)
    if explicit_revisit:
        # A revisit always re-examines what no module covers, which only the agent can see.
        _owe(queue, "stacks", "revisit: anything in play no module covers?")
        _owe(queue, "frameworks", "revisit: anything in play no module covers?")
    if revisit or explicit_revisit:
        s.openings.append("revisit")

    # 6. An explicit repair names its block.
    if len(args) >= 2 and args[0] == "repair":
        _owe(queue, args[1], "repair requested")
        s.openings.append("repair")

    # 7. An explicit revisit that found nothing owed: what was only ever defaulted, first.
    if explicit_revisit and not found_anything:
        for b in WALKED:
            if (entries.get(b.id) or {}).get("state") == "defaulted":
                _owe(queue, b.id, "only ever defaulted")

    # 8. The upgrade notes newer than the oldest review — the agent maps items to blocks.
    oldest = min((v for v in s.ledger_at.values() if v), key=_version_key, default=None)
    floor = (
        stamped
        if oldest is None
        else (min(stamped, oldest, key=_version_key) if stamped else oldest)
    )
    if exists and (
        s.upgrade in ("unstamped", "behind")
        or (oldest and _version_key(oldest) < _version_key(installed))
    ):
        s.notes = [asdict(n) for n in sections_after(floor)]
        if s.upgrade in ("unstamped", "behind"):
            s.openings.append("upgrade")

    # 9. The stamp.
    s.stamp_due = bool(queue) or s.upgrade != "current"
    if exists and s.upgrade == "patch-behind" and not queue:
        s.openings.append("re-stamp")

    order = {b.id: b.number for b in BLOCKS}
    s.queue = [
        {"block": k, "number": order[k], "reasons": v}
        for k, v in sorted(queue.items(), key=lambda kv: order[kv[0]])
    ]
    # 10. Nothing at all.
    s.nothing_owed = not queue and not s.stamp_due and not s.notes and not args
    return s


def main(argv: list[str] | None = None) -> int:
    args = [a for a in (sys.argv[1:] if argv is None else argv) if a != "--json"]
    if (
        args
        and args[0] == "repair"
        and (len(args) < 2 or args[1] not in {b.id for b in WALKED})
    ):
        print(
            json.dumps(
                {"error": f"repair needs a block: {', '.join(b.id for b in WALKED)}"}
            )
        )
        return 2
    try:
        state = compute(args)
    except LedgerError as exc:
        print(json.dumps({"refuse": f"{exc} — fix or remove it, then run /harness-setup again"}))
        return 2
    print(json.dumps(asdict(state), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
