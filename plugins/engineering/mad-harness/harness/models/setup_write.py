"""The ONE route by which /harness-setup writes `harness.yaml` — and only for a person.

WHO IS ATTENDING, NOT WHICH KEY. `check_commands._NORMATIVE` lists the keys no agent may
write — security, signals, testing, lanes, slug, name and more — and that is most of what
setup writes. So the guard cannot be the key: it is whether anyone is there to confirm. It
refuses on facts the harness itself writes: a worker's `.swarm-env` exports `TRACKER_ACTOR`
and `SWARM_LANE` (`worker.env_block`, asserted by `probe_worktree.check_env`); a linked
worktree is a worker's, not the primary checkout; and the dispatcher puts worktrees under
`dispatch.WORKTREE_ROOT`. A Claude Code session's own variables are reported but never
refuse alone — an operator's interactive session sets them too (`probe_compat` strips them
from a child for exactly that reason). Beyond the guard the posture is the corpus's usual
one: visibility, not prevention — every write is a reviewable diff.

ONE BLOCK'S KEYS AT A TIME. A block may write only the top-level keys `setup_blocks` gives
it, so a `security` value cannot ride in on the `areas` block's write.

THE LEDGER IS WRITTEN ON THE ANSWER. A walk calls this twice per block: the derived values
first (no state — the ledger is untouched, so an interrupted pass resumes at the block the
owner never answered), then the answer (`confirmed` or `declined`). Only the lab's
derive-only handle records `defaulted`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from . import project as _project
from .setup_blocks import UNOWNED, by_id, owners, stack_key
from .setup_ledger import LedgerError
from .yaml_edit import Unanchorable, remove_block, set_block

#: Exit codes the skill branches on.
EXIT_REFUSED = 3  # cannot anchor: the fragment is printed for the owner to paste
EXIT_UNATTENDED = 4  # nobody is there to confirm

#: How list entries are recognised across a rewrite, so a changed entry is edited in place
#: and keeps its comments rather than being rendered fresh.
IDENTITY = {
    "stacks": stack_key,
    "areas": lambda a: a.get("path") if isinstance(a, dict) else a,
}


class Unattended(RuntimeError):
    """A worker, or a worktree: there is nobody to confirm a write to policy."""


def refuse_unattended(
    env: dict[str, str] | None = None, cwd: Path | None = None
) -> str | None:
    """Why this process must not write `harness.yaml`, or None."""
    from .dispatch import WORKTREE_ROOT

    env = os.environ if env is None else env
    # The wrappers `cd` into the harness before Python starts, so the process's own cwd is
    # always the plugin's; the caller's directory is the one each wrapper preserved.
    cwd = (cwd or Path(env.get("MAD_HARNESS_CALLER_PWD") or Path.cwd())).resolve()
    for var in ("TRACKER_ACTOR", "SWARM_LANE"):
        if env.get(var):
            return f"{var} is set — this is a dispatched worker, and nobody is here to confirm"
    # Every agent the dispatcher runs — a headless campaign orchestrator in the PRIMARY
    # checkout included, which carries no `.swarm-env` — gets this from
    # `dispatch.build_env`; an interactive session never has it.
    if env.get("HARNESS_ROOT"):
        return "HARNESS_ROOT is set — this is a dispatched agent, and nobody is here to confirm"
    try:
        cwd.relative_to(WORKTREE_ROOT.resolve())
        return f"running under {WORKTREE_ROOT} — a worker's worktree, not the primary checkout"
    except ValueError:
        pass
    out = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--git-dir", "--git-common-dir"],
        capture_output=True,
        text=True,
    )
    dirs = out.stdout.split() if out.returncode == 0 else []
    if len(dirs) != 2:
        # FAILS CLOSED: a guard that waves a write through whenever it cannot tell is no guard.
        return f"cannot tell whether {cwd} is the primary checkout (git rev-parse failed)"
    git_dir, common = ((cwd / d).resolve() for d in dirs)
    if git_dir != common:
        return "inside a linked worktree — setup writes the primary checkout's config only"
    return None


def unattended_hint(env: dict[str, str] | None = None) -> str | None:
    """The belt: a Claude Code session with no terminal. Reported, never a refusal on its own
    — an operator's interactive session sets the same variables."""
    env = os.environ if env is None else env
    session = env.get("CLAUDECODE") or any(k.startswith("CLAUDE_CODE_") for k in env)
    if session and not sys.stdin.isatty():
        return "a Claude Code session with no terminal attached — fine for an owner's session, suspicious for anything else"
    return None


@dataclass
class Result:
    changed: bool
    block: str
    ledger: dict | None = None


def _check_keys(block_id: str, values: dict, remove: list[str]) -> None:
    block = by_id(block_id)
    for key in [*values, *remove]:
        if key in UNOWNED:
            raise PermissionError(
                f"`{key}:` is never written by setup — {key} is the owner's alone"
            )
        if block not in owners(key):
            raise PermissionError(
                f"block {block_id!r} may not write `{key}:`; it writes {', '.join(block.keys)}"
            )


def _confine(block_id: str, values: dict, current: dict) -> dict:
    """Keep a block's write inside its own part of a SHARED key (`setup_blocks` docstring).

    `declined:` — the stacks block owns `declined.stacks`, the frameworks block
    `declined.frameworks`; whatever the other block wrote is carried over untouched. `stacks:`
    — the stacks block decides WHICH toolchains, at which roots, and every command override
    it does not mention is carried over by `name@root`; the commands block may change only
    commands, and is refused if its value would change which toolchains are declared."""
    from .setup_blocks import _stack_entries, by_id, extract

    out = dict(values)
    if "declined" in out:
        own = {"stacks": "stacks", "frameworks": "frameworks"}.get(block_id)
        given = out["declined"] or {}
        if set(given) - {own}:
            raise PermissionError(f"block {block_id!r} may write only `declined.{own}:`")
        merged = {k: v for k, v in (current.get("declined") or {}).items() if k != own}
        if given.get(own):
            merged[own] = given[own]
        out["declined"] = merged
    if "stacks" in out and block_id == "stacks":
        have = _stack_entries(current)
        entries = []
        for e in out["stacks"] or []:
            old = have.get(stack_key(e)) or {}
            if old.get("commands") and not (isinstance(e, dict) and "commands" in e):
                e = {**(e if isinstance(e, dict) else {"name": e}), "commands": old["commands"]}
            entries.append(e)
        out["stacks"] = entries
    if "stacks" in out and block_id == "commands":
        stacks = by_id("stacks")
        if extract({**current, "stacks": out["stacks"]}, stacks)["stacks"] != extract(current, stacks)["stacks"]:
            raise PermissionError(
                "block 'commands' may change only each stack's commands, not which "
                "toolchains are declared — that is the stacks block's"
            )
    return out


def _apply(text: str, values: dict, remove: list[str]) -> str:
    for key, value in values.items():
        text = set_block(text, key, value, identity=IDENTITY.get(key))
    for key in remove:
        text = remove_block(text, key)
    return text


def _replace(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.setup-tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _record(block_id: str, state: str, raw: dict, because: str | None) -> dict:
    from .discover import dependencies, roots
    from .setup_ledger import record, snapshot

    deps = None
    if block_id == "frameworks" and state == "confirmed":
        # The snapshot is computed HERE, never transcribed by the agent: it is what
        # `check_project` compares against to warn of a framework added later (spec D-25).
        candidate_roots, _ = roots()
        deps = snapshot({r: d for r in candidate_roots if (d := dependencies(r))})
    return record(block_id, state, raw, because=because, dependencies=deps)


def write_block(
    block_id: str,
    values: dict,
    *,
    state: str | None = None,
    because: str | None = None,
    remove: list[str] | None = None,
    path: Path | None = None,
) -> Result:
    """Write one block's values; record the owner's answer when `state` is given."""
    reason = refuse_unattended()
    if reason:
        raise Unattended(reason)
    from .setup_ledger import read

    remove = list(remove or [])
    _check_keys(block_id, values, remove)
    if state:
        read()  # an unreadable ledger refuses BEFORE the config changes, never after
    path = path or _project.PROJECT_FILE
    before = path.read_text()
    values = _confine(block_id, values, yaml.safe_load(before) or {})
    after = _apply(before, values, remove)
    if after != before:
        _replace(path, after)
    entry = (
        _record(block_id, state, yaml.safe_load(after) or {}, because)
        if state
        else None
    )
    return Result(after != before, block_id, entry)


# --- first run ----------------------------------------------------------------------------

HEADER = """\
# harness.yaml — what makes this harness about THIS repository.
#
# Written by /harness-setup against plugin {version}, from what the repository already
# said; every block was then shown to the owner to confirm, change or extend. Run
# /harness-setup again to revisit a block — it picks up where it was left. The comments
# below are the template's: they explain each block.
"""


def render_first_run(drafts: list[Any], path: Path | None = None) -> Path:
    """Render a fresh `harness.yaml` from the template and every block's derived values.

    Valid from the first write — `name`, `slug` and `areas` are always derived — which is what
    makes a first run safe to abandon. No template value survives: a key no derivation
    supplied is removed (its comment header stays, to explain it), so nothing illustrative
    from the template's imaginary project is ever mistaken for a fact about this one. The
    stamp is absent until block 13. The ledger is not touched."""
    from .setup_derive import TEMPLATE

    reason = refuse_unattended()
    if reason:
        raise Unattended(reason)
    path = path or _project.PROJECT_FILE
    if path.exists():
        raise FileExistsError(
            f"{path} exists — a first run renders only where there is no config"
        )
    template = TEMPLATE.read_text()
    lines = template.splitlines(keepends=True)
    first_key = next(i for i, ln in enumerate(lines) if ln.startswith("name:"))
    text = (
        HEADER.format(version=_project.plugin_version())
        + "\n"
        + "".join(lines[first_key:])
    )
    supplied: dict[str, Any] = {}
    for draft in drafts:
        supplied.update(draft.values)
    text = _apply(text, supplied, [])
    leftover = [k for k in (yaml.safe_load(text) or {}) if k not in supplied]
    text = _apply(text, {}, leftover)
    _replace(path, text)
    _project.load(path)  # must load; a first run that leaves an invalid file is a bug
    return path


def derive_only() -> list[str]:
    """The lab's handle (never documented for owners): write every derived value, leave the
    owed ones absent, record `defaulted`. The result honestly WARNs rather than fails."""
    from .setup_derive import context, derive_all
    from .setup_ledger import record

    ctx = context()
    drafts = derive_all(ctx)
    if not _project.PROJECT_FILE.exists():
        render_first_run(drafts)
    else:
        for d in drafts:
            if d.values:
                write_block(d.block, d.values)
    raw = yaml.safe_load(_project.PROJECT_FILE.read_text()) or {}
    for d in drafts:
        record(d.block, "defaulted", raw)
    return [d.block for d in drafts]


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="write.sh", description="Write one setup block to harness.yaml."
    )
    ap.add_argument("block", nargs="?")
    ap.add_argument("--state", choices=("confirmed", "declined"))
    ap.add_argument("--because")
    ap.add_argument(
        "--remove", default="", help="comma-separated top-level keys to remove"
    )
    ap.add_argument("--render-first-run", action="store_true")
    ap.add_argument("--derive-only", action="store_true")
    args = ap.parse_args(argv)
    try:
        if args.derive_only:
            print(json.dumps({"defaulted": derive_only()}))
            return 0
        if args.render_first_run:
            from .setup_derive import context, derive_all

            print(
                json.dumps({"rendered": str(render_first_run(derive_all(context())))})
            )
            return 0
        if not args.block:
            ap.error("name a block, or --render-first-run")
        raw = sys.stdin.read().strip()
        values = json.loads(raw) if raw else {}
        result = write_block(
            args.block,
            values,
            state=args.state,
            because=args.because,
            remove=[k for k in args.remove.split(",") if k],
        )
        out = {"changed": result.changed, "block": result.block, "ledger": result.ledger}
        hint = unattended_hint()
        if hint:
            out["hint"] = hint  # the belt: reported, never a refusal on its own
        print(json.dumps(out))
        return 0
    except Unattended as exc:
        print(json.dumps({"refused": str(exc), "unattended": True}))
        return EXIT_UNATTENDED
    except Unanchorable as exc:
        print(json.dumps({"refused": exc.why, "fragment": exc.fragment}))
        return EXIT_REFUSED
    except LedgerError as exc:
        print(json.dumps({"refused": f"{exc} — fix or remove it; nothing was written"}))
        return 2
    except (PermissionError, FileExistsError, ValueError, KeyError) as exc:
        print(json.dumps({"refused": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
