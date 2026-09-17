"""Verify a staged epic's decision register against the tracker, and check contention.

The register owns the epic<->decision ASSOCIATION — the tracker does not hold it, since
almost no decision record carries a parent. The tracker owns STATUS. This keeps the
register's cache honest, so a settled decision cannot sit in `open` blocking an epic that
is no longer blocked, and a still-open one cannot hide in `settled` and silently unblock a
fold-in.

READS YAML FRONTMATTER, NOT MARKDOWN TABLES. The table-parsing version took three rounds
to stop misreading columns; that is why the register is structured.

SILENCE IS NOT A PASS. An epic with no register did not pass the decision gate — the gate
did not run. Saying so out loud is the difference between "checked and clean" and "never
checked", and only one of those is true.
"""

from __future__ import annotations

import glob
import os
import re
import sys

import tracker
from tracker.port import CLOSED

try:
    import yaml
except ImportError:  # pragma: no cover - the harness declares PyYAML
    yaml = None


def _proposed() -> str:
    """The staging directory, from config.

    No default: a wrong guess globs a path that does not exist, finds nothing, and
    reports clean — the failure this check exists to catch, turned on itself.
    """
    from models.project import load

    d = (load().paths or {}).get("proposed")
    if not d:
        raise tracker.TrackerError(
            "harness.yaml declares no paths.proposed — cannot locate staged files"
        )
    return d


def _front(path: str) -> dict | None:
    text = open(path, encoding="utf-8").read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return None
    return yaml.safe_load(m.group(1)) or {}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    sel = args[0] if args else ""
    if yaml is None:
        print("PyYAML required", file=sys.stderr)
        return 2

    try:
        proposed = _proposed()
    except tracker.TrackerError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    store = tracker.task_store()

    def status_of(tid: str) -> str | None:
        try:
            t = store.show(tid)
        except tracker.TrackerError:
            return None
        return t.status if t else None

    fail = False
    # IN THE PROJECT, FOR EITHER ID FORM. This globbed relative to the process cwd — the
    # harness, after the wrapper's cd — and with the id exactly as given, so a prefixed
    # id found nothing and reported "NO REGISTER" as if the epic predated the flow.
    from models.resolve import REPO

    from .staging import id_forms, known_prefix

    forms = id_forms(sel, known_prefix()) if sel else ("",)
    registers = sorted(
        {r for form in forms for r in glob.glob(str(REPO / proposed / f"{form}*" / "decisions.md"))}
    )
    if sel and not registers:
        print(f"NO REGISTER for {sel} — this epic predates the decision-register flow.")
        print("  The decision gate did NOT run. Before proceeding, check by hand:")
        print("    tk.sh list --type decision --status open")
        print("  Any open decision binding this epic parks it, register or not.")
        return 0

    for reg in registers:
        folder = os.path.dirname(reg)
        print(f"── {reg}")
        fm = _front(reg)
        if fm is None:
            print("   ✗ no YAML frontmatter — the register IS the frontmatter")
            fail = True
            continue

        for row in fm.get("open") or []:
            tid = row.get("task") or row.get("id")
            st = status_of(tid)
            if st is None:
                print(f"   ✗ {tid} in `open` but not found in the tracker")
                fail = True
            elif st == CLOSED:
                print(
                    f"   ✗ {tid} in `open` but the tracker says closed — move it to "
                    f"`settled` with its resolution"
                )
                fail = True
            adr = row.get("adr")
            if adr and not os.path.exists(os.path.join(folder, adr)):
                print(f"   ✗ {tid} cites {adr}, which does not exist")
                fail = True

        for row in fm.get("settled") or []:
            tid = row.get("task") or row.get("id")
            st = status_of(tid)
            if st is None:
                print(f"   ✗ {tid} in `settled` but not found in the tracker")
                fail = True
            elif st != CLOSED:
                print(f"   ✗ {tid} in `settled` but the tracker says {st} — it still blocks")
                fail = True
            if not row.get("resolution"):
                print(f"   ✗ {tid} settled with no resolution recorded")
                fail = True

        cited = {r.get("adr") for r in (fm.get("open") or []) if r.get("adr")}
        for adr in glob.glob(os.path.join(folder, "adr-*.md")):
            if os.path.basename(adr) not in cited:
                print(f"   ✗ {os.path.basename(adr)} exists but no register row cites it")
                fail = True

        n_open = len(fm.get("open") or [])
        tail = "  ← epic cannot fold in or close" if n_open else ""
        print(f"   OK — {n_open} open, {len(fm.get('settled') or [])} settled{tail}")

    # Cross-proposal contention: two staged proposals editing the same doc. Cheap because
    # proposals are short-lived — fold-in at epic START means few coexist, and a long
    # queue of open proposals is itself the signal that specification is running ahead.
    lands: dict[str, list[str]] = {}
    for prop in sorted(glob.glob(f"{proposed}/*/proposal.md")):
        fm = _front(prop) or {}
        if str(fm.get("status", "")) == "folded-in":
            continue  # applied, not pending
        for doc in fm.get("lands_in") or []:
            lands.setdefault(doc, []).append(os.path.basename(os.path.dirname(prop)))
    clash = {k: v for k, v in lands.items() if len(v) > 1}
    if clash:
        print("\n── cross-proposal contention")
        for doc, eps in sorted(clash.items()):
            print(f"   ! {doc} — claimed by {', '.join(eps)}")
        print("   Name which lands first and gate the other.")

    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
