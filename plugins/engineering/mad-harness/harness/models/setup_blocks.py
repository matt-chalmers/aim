"""The setup blocks: the ONE list of what /harness-setup walks, in what order, owning what.

WHY A REGISTRY IN CODE. Setup is a walk over `harness.yaml` in dependency order, and four
things need that order and must never disagree about it: the skill's prose (one section per
block), the owed queue and its `[n/13]` counter (`setup_state`), the ledger's per-block hash
(`setup_ledger`), and the writer's refusal to let one block write another's keys
(`setup_write`). Stated in prose, the order would be a second source of truth the moment
anything else needed it — so it is stated here once, and a test pins the skill's headings
to it.

KEY OWNERSHIP IS BY TOP-LEVEL KEY, WITH TWO SHARED KEYS SPLIT BY `extract`. `stacks:` holds
both the toolchain decision (block 2) and each toolchain's commands (block 4), because a
command override lives inside its stack entry; `declined:` holds the stack and framework
suppressions (blocks 2 and 3). Splitting them is what lets the pre-flight's command repair
record itself on `commands` without touching the owner's confirmation of which toolchains
exist (D-23).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Block:
    id: str
    number: int
    title: str
    #: Top-level keys this block may write. Two are shared — see the module docstring.
    keys: tuple[str, ...]
    #: Whether the ledger hashes this block. `models` and `stamp` are acknowledgements: an
    #: owner's later model patch, or a re-stamp, must never read as an unconfirmed edit.
    hashed: bool = True


BLOCKS: tuple[Block, ...] = (
    Block("identity", 1, "name and slug", ("name", "slug")),
    Block("stacks", 2, "toolchains", ("stacks", "declined")),
    Block("frameworks", 3, "frameworks", ("frameworks", "declined")),
    Block("commands", 4, "stack commands", ("stacks",)),
    Block("paths", 5, "paths and structural roles", ("paths", "layout")),
    Block(
        "tracker", 6, "tracker, merge slot, gitignore", ("tracker", "beads", "swarm")
    ),
    Block("security", 7, "security surface", ("security",)),
    Block("areas", 8, "area map", ("areas",)),
    Block("testing", 9, "testing and lenses", ("testing", "lenses")),
    Block("lanes", 10, "lanes, ports, fidelity", ("lanes", "ports", "fidelity")),
    Block("domain", 11, "domain vocabulary and signals", ("domain", "signals")),
    Block(
        "models",
        12,
        "model config",
        ("strengths", "activities", "providers"),
        hashed=False,
    ),
    Block("stamp", 13, "stamp and prove", ("harness",), hashed=False),
)

#: Top-level keys setup never writes. `permissions` grows only by answering a `Permission:`
#: record; `dispatch` holds measured cost levers. Listed so that a NEW schema key fails
#: `test_every_top_level_key_has_an_owner` until someone decides who owns it.
UNOWNED: tuple[str, ...] = ("permissions", "dispatch")


def by_id(block_id: str) -> Block:
    for b in BLOCKS:
        if b.id == block_id:
            return b
    raise KeyError(
        f"no setup block {block_id!r}; known: {', '.join(b.id for b in BLOCKS)}"
    )


def owners(top_key: str) -> tuple[Block, ...]:
    """The blocks permitted to write `top_key` — empty for an unowned or unknown key."""
    return tuple(b for b in BLOCKS if top_key in b.keys)


def uncovered(top_keys: set[str]) -> set[str]:
    """Keys no block owns and `UNOWNED` does not list — a schema key nobody decided about."""
    return {k for k in top_keys if not owners(k) and k not in UNOWNED}


def stack_key(entry: Any) -> str:
    """A stack entry's identity, `name@root` — a bare string is a root-layout entry."""
    if isinstance(entry, dict):
        return f"{entry.get('name')}@{entry.get('root') or '.'}"
    return f"{entry}@."


def _stack_entries(raw: dict) -> dict[str, dict]:
    """Every declared stack as a mapping keyed `name@root`. A bare `- python-uv` and the
    mapping `write_repair` promotes it to are the same entry, so the promotion changes no
    hash."""
    out: dict[str, dict] = {}
    for entry in raw.get("stacks") or []:
        if isinstance(entry, dict):
            d = dict(entry)
            d.setdefault("root", ".")
        else:
            d = {"name": str(entry), "root": "."}
        out[stack_key(d)] = d
    return out


def extract(raw: dict, block: Block) -> Any:
    """The block's sub-tree of a parsed config — the one projection the hash, the writer's
    post-condition and the derive-versus-current comparison all use."""
    raw = raw or {}
    declined = raw.get("declined") or {}
    if block.id == "stacks":
        entries = {
            k: {f: v for f, v in d.items() if f != "commands"}
            for k, d in _stack_entries(raw).items()
        }
        return {"stacks": entries, "declined": declined.get("stacks") or {}}
    if block.id == "commands":
        return {
            k: d["commands"]
            for k, d in _stack_entries(raw).items()
            if d.get("commands")
        }
    if block.id == "frameworks":
        return {
            "frameworks": raw.get("frameworks"),
            "declined": declined.get("frameworks") or {},
        }
    return {k: raw[k] for k in block.keys if k in raw}
