"""`.harness/setup.json` — which blocks of `harness.yaml` a human has reviewed, and when.

IT STORES NO CONFIG VALUES, which is what stops it becoming a second source of truth: it
cannot disagree with `harness.yaml` about what the config SAYS, only about whether anyone
looked. Each block's entry is a state, the plugin version it was reviewed at (`at`), a date,
a hash of the block's parsed value, and an optional reason. The one non-review datum is the
`frameworks` entry's dependency snapshot — repository facts, not config (spec D-25).

FOUR STATES, NO `owed`. Owed is recomputed on every run (`setup_state`), so storing it would
let the ledger claim a block is owed that the config now supplies. A block with NO entry is
always owed — that one rule resumes an interrupted pass and covers every config written
before the ledger existed (D-22).

THE HASH IS OVER THE PARSED VALUE (`setup_blocks.extract`, canonically serialised), never
the text: reformatting or adding a comment demotes nothing, a changed value does.

TRACKED, NOT IGNORED. Both .gitignores here ignore `.harness/run/`, `.harness/tasks/` and
`.harness/cache/` rather than the tree, because project config a repository writes on
purpose must travel with it. A review record on one machine is no record.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from . import project as _project
from .setup_blocks import BLOCKS, Block, by_id, extract

LEDGER_VERSION = 1
STATES = ("confirmed", "defaulted", "declined", "repaired")


class LedgerError(RuntimeError):
    """The ledger exists but cannot be trusted — setup stops; the config check warns."""


def ledger_path() -> Path:
    return _project.REPO / ".harness" / "setup.json"


def read(path: Path | None = None) -> dict:
    """The ledger, or an empty one when the file is absent — absence is a valid state."""
    path = path or ledger_path()
    if not path.exists():
        return {"version": LEDGER_VERSION, "blocks": {}}
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise LedgerError(f"{path} is not readable JSON: {exc}") from exc
    if (
        not isinstance(d, dict)
        or d.get("version") != LEDGER_VERSION
        or not isinstance(d.get("blocks"), dict)
    ):
        raise LedgerError(f"{path} is not a version-{LEDGER_VERSION} setup ledger")
    return d


def _canonical(value: Any) -> Any:
    """Mapping keys as strings, so `{3000: web, backend: 8000}` sorts rather than raising —
    YAML keys need not share a type, and json's `sort_keys` cannot order an int and a str.
    The key's type is kept in the string, so `1` and `"1"` stay distinct."""
    if isinstance(value, dict):
        return {f"{type(k).__name__}:{k}": _canonical(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    return value


def canonical_hash(raw: dict, block: Block) -> str | None:
    if not block.hashed:
        return None
    body = json.dumps(
        _canonical(extract(raw, block)), sort_keys=True, separators=(",", ":"), default=str
    )
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


def record(
    block_id: str,
    state: str,
    raw: dict,
    *,
    because: str | None = None,
    dependencies: dict[str, list[str]] | None = None,
    path: Path | None = None,
) -> dict:
    """Write one block's entry and return it. Atomic, sorted, one line per field, so a review
    is a small readable diff."""
    if state not in STATES:
        raise ValueError(
            f"unknown ledger state {state!r}; expected one of {', '.join(STATES)}"
        )
    if state == "declined" and not (because or "").strip():
        raise ValueError(
            "a declined block needs a reason — a bare decline reads as an oversight"
        )
    block = by_id(block_id)
    path = path or ledger_path()
    ledger = read(path)
    entry: dict[str, Any] = {
        "state": state,
        "at": _project.plugin_version(),
        "date": date.today().isoformat(),
        "hash": canonical_hash(raw, block),
    }
    if because:
        entry["because"] = because.strip()
    if dependencies is not None:
        entry["dependencies"] = {
            root: sorted(names) for root, names in sorted(dependencies.items())
        }
    ledger["blocks"][block_id] = entry
    _write(path, ledger)
    return entry


def _write(path: Path, ledger: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".setup.", suffix=".json")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def hash_stale(raw: dict, ledger: dict) -> list[str]:
    """Confirmed blocks whose value changed since — the owner's hand edit, unconfirmed.
    Only `confirmed` demotes: a `repaired` block was changed by a machine on purpose, and
    `defaulted`/`declined` were never a human's yes to a value."""
    out = []
    for b in BLOCKS:
        entry = (ledger.get("blocks") or {}).get(b.id) or {}
        if (
            entry.get("state") == "confirmed"
            and b.hashed
            and entry.get("hash") != canonical_hash(raw, b)
        ):
            out.append(b.id)
    return out


def dependency_drift(
    ledger: dict, current: dict[str, dict[str, list[str]]]
) -> dict[str, list[str]]:
    """Per root, direct dependencies present now and absent from block 3's snapshot.

    `current` is `{root: {manifest: [names]}}` as `discover.dependencies` returns per root.
    No snapshot means nothing to compare against — say nothing rather than call every
    dependency new."""
    snapshot = ((ledger.get("blocks") or {}).get("frameworks") or {}).get(
        "dependencies"
    )
    if snapshot is None:
        return {}
    out: dict[str, list[str]] = {}
    for root, manifests in sorted(current.items()):
        now = {n for names in manifests.values() for n in names}
        new = sorted(now - set(snapshot.get(root) or []))
        if new:
            out[root] = new
    return out


def snapshot(current: dict[str, dict[str, list[str]]]) -> dict[str, list[str]]:
    """The names-only, per-root form the ledger stores."""
    return {
        root: sorted({n for names in manifests.values() for n in names})
        for root, manifests in current.items()
        if manifests
    }
