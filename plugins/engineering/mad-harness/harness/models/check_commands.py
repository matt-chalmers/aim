"""Verify each stack's declared commands, and repair the ones that have rotted.

WHY VERIFY AT ALL, WHEN A WRONG COMMAND FAILS LOUDLY. Because it fails loudly
LATE and N TIMES. A stale `test_scoped` is rediscovered by every worker in every
wave, each burning turns on it — and a worker that gives up returns `BLOCKED`,
which `escalate.py` classifies as a reasoning failure and escalates to a more
expensive tier. A stronger model cannot fix a config error. One ~3s probe at
pre-flight replaces all of that.

WHY NOT JUST TRUST THEM. The harness already grades its config by failure mode,
and treats the two ends completely differently:

    env (DB_NAME)   SILENT   - suite goes green while workers corrupt each other.
                               Generated, never typed; a whole test file guards it.
    commands        LOUD     - a wrong command errors; it does not produce a
                               wrong answer. Guarded by nothing at all.

Loud failure is why this is a probe rather than a gate. It is still worth probing,
because late-and-N-times is expensive even when it is obvious.

THE LADDER, cheapest first, bailing early — the shape `probe_compat.py` uses:

    1. toolchain present   free      is `uv`/`npm` even on PATH?
    2. probe the command   ~3s       run `commands.verify`; clean -> done
    3. repair              seconds   derive candidates FROM THE REPO, probe each,
                                     write the first that passes to harness.yaml
    4. block               -         nothing works: exit non-zero, naming all tried

Only rung 4 stops a run. A command that can be repaired is repaired, because the
alternative — halting a campaign over a renamed npm script — costs more than it
saves.

TWO BOUNDARIES THIS MUST NOT CROSS.

  * **Repair writes to `harness.yaml`, never to a shipped stack module.** Which
    command a project runs is the PROJECT's fact; the module ships a default.
    Writing into the plugin would make one repo's choice everybody's.
  * **Repair touches DESCRIPTIVE config only.** `security.*`, `signals.*` and
    `testing.coverage` are policy the agents are judged against — an agent able
    to edit those could disable the gate it is about to face. `_REPAIRABLE` is
    that boundary, and a test asserts it holds.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from .commands import OK, PROBE_TIMEOUT, resolve, run_key, tool_of
from .project import PROJECT_FILE, Project, Stack, load
from .resolve import REPO

#: The one config section this module writes into. A bare key like `test_scoped`
#: is a command key by construction and lands under `commands:`.
_REPAIRABLE = ("commands",)

#: Sections this module must NEVER write, whatever it is asked to. These are the
#: policy an agent is JUDGED BY — the security surface that decides whether L4
#: fires, the signal baselines, the coverage bar, the lane caps measured off the
#: owner's hardware. A repair mechanism able to reach them is one a worker could
#: use to switch off the lens about to judge it. Pinned by
#: test_repair_refuses_a_normative_key.
_NORMATIVE = (
    # An agent may REQUEST a grant; only the operator may write one. An agent able to
    # edit this could approve its own request, which is the laundering pattern the
    # permission queue exists to avoid.
    "permissions",
    "security",
    "signals",
    "testing",
    "lanes",
    "beads",
    "swarm",
    "slug",
    "name",
    # Which tier a lens runs on. Moving the lens that is about to judge a worker down
    # to the cheap tier is a quieter version of switching it off, and it is the owner's
    # A/B switch — see `Project.tiers`.
    "agent_tiers",
)

#: Command keys worth probing. `test_scoped` is the one workers actually use, so
#: it leads; a project that declares only `test` still gets that checked.
PROBE_KEYS = ("verify", "test_scoped", "test", "lint", "typecheck")


@dataclass
class Finding:
    """One stack's verdict, and the repair if there was one."""

    stack: str
    status: str  # ok | unproven | repaired | broken
    detail: str = ""
    key: str = ""
    was: str = ""
    now: str = ""

    def line(self) -> str:
        mark = {
            "ok": "ok      ",
            "unproven": "UNPROVEN",
            "repaired": "REPAIRED",
            "broken": "BROKEN  ",
        }[self.status]
        head = f"  [{mark}] {self.stack}"
        if self.status == "repaired":
            return f"{head}  {self.key}: {self.was!r} -> {self.now!r}"
        return f"{head}  {self.detail}" if self.detail else head


# --- rung 3: candidates, derived from the repository's own declarations -------
#
# NEVER a built-in runner list. A hardcoded ["pytest", "vitest", "go test"] would
# reintroduce exactly the coupling that made these modules unusable outside one
# monorepo: the harness would be asserting which runners exist. Everything below
# reads a file the project already wrote, so a project using a runner nobody here
# has heard of is still repairable.


def _json_scripts(rel: str) -> dict[str, str]:
    p = REPO / rel
    if not p.is_file():
        return {}
    try:
        return dict(json.loads(p.read_text()).get("scripts") or {})
    except (json.JSONDecodeError, OSError, AttributeError):
        return {}


def _ci_commands() -> list[str]:
    """Every `run:` line in the CI workflows.

    CI is the most authoritative source available: it is the command the project
    already trusts enough to gate merges on. Read as text rather than as YAML so
    a workflow this harness cannot parse still contributes.
    """
    out: list[str] = []
    wf = REPO / ".github" / "workflows"
    if not wf.is_dir():
        return out
    for f in sorted(wf.glob("*.y*ml")):
        try:
            text = f.read_text()
        except OSError:
            continue
        for m in re.finditer(r"^\s*run:\s*(.+)$", text, re.M):
            line = m.group(1).strip().strip("|>").strip()
            if line and "\n" not in line:
                out.append(line)
    return out


def _make_targets() -> list[str]:
    mk = REPO / "Makefile"
    if not mk.is_file():
        return []
    try:
        text = mk.read_text()
    except OSError:
        return []
    return [f"make {m.group(1)}" for m in re.finditer(r"^([a-z][\w-]*):", text, re.M)]


def candidates(stack: Stack, key: str) -> list[str]:
    """Plausible replacements for a rotted command, best source first.

    Ordered by how much the project has already committed to each: a CI line is a
    command the project gates merges on; an npm script is one it wrote down; a
    Makefile target is one it wraps. All three are facts in the tree.
    """
    want_test = key.startswith("test")
    out: list[str] = []

    # 1. CI — the strongest signal, filtered to lines that plausibly do this job.
    verb = "test" if want_test else key
    out += [c for c in _ci_commands() if verb in c]

    # 2. Package scripts, for whatever declares them, run through the stack's own
    #    invocation prefix so the result is shaped like the module's other commands.
    prefix = tool_of(resolve(stack, "test") or resolve(stack, "lint") or "")
    for rel in ("package.json", stack.at("package.json")):
        for name in _json_scripts(rel):
            if verb in name and prefix:
                out.append(f"{prefix} run {name}")

    # 3. Make targets that name the job.
    out += [t for t in _make_targets() if verb in t]

    seen: set[str] = set()
    uniq = []
    for c in out:
        c = c.strip()
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


# --- writing the repair -------------------------------------------------------


def _stack_entry_span(text: str, stack: str) -> tuple[int, int] | None:
    """The line span of one stack's entry under `stacks:`, if it is a mapping."""
    # A trailing comment is normal in a hand-written config, so the name may not
    # end the line. Anchoring to `$` without allowing one is why this missed.
    m = re.search(
        rf"^([ \t]*)-[ \t]*name:[ \t]*{re.escape(stack)}[ \t]*(?:#.*)?$", text, re.M
    )
    if not m:
        return None
    indent = len(m.group(1))
    start = m.end()
    for nxt in re.finditer(r"^(\s*)(-|\w)", text[start:], re.M):
        if len(nxt.group(1)) <= indent:
            return start, start + nxt.start()
    return start, len(text)


def _set_in_entry(body: str, key: str, command: str, stamp: str) -> str | None:
    """Set `key: command` inside one stack entry's body. None = already correct.

    Three cases, in order: the key is already there (replace it in place), a
    `commands:` block exists but lacks the key (insert under it), or neither
    exists (open a block). Kept separate from `write_repair` so each case stays
    readable — this is the part that must not mangle a commented file.
    """
    line = re.compile(rf"^([ \t]+){re.escape(key)}:[ \t]*([^#\n]*?)[ \t]*(#.*)?$", re.M)
    m = line.search(body)
    if m:
        if m.group(2).strip() == command:
            return None
        return line.sub(f"{m.group(1)}{key}: {command}{stamp}", body, count=1)

    cm = re.search(r"^([ \t]+)commands:[ \t]*$", body, re.M)
    if cm:
        pad = cm.group(1) + "  "
        return body[: cm.end()] + f"\n{pad}{key}: {command}{stamp}" + body[cm.end() :]

    # No commands block yet. Indent one level past the entry's own keys, which
    # `_stack_entry_span` guaranteed are more-indented than the `- name:` line.
    first = re.search(r"^([ \t]+)\S", body, re.M)
    pad = first.group(1) if first else "    "
    return f"\n{pad}commands:\n{pad}  {key}: {command}{stamp}" + body


def write_repair(stack: str, key: str, command: str, path: Path | None = None) -> bool:
    """Record a repaired command in `harness.yaml`. Returns whether it wrote.

    ANCHORED EDIT, NOT A YAML ROUND-TRIP. `yaml.safe_dump` discards every comment
    and reorders keys, and a real `harness.yaml` is mostly comments explaining why
    each value is what it is — losing them would cost far more than the repair is
    worth. `check_config.py:sync()` already solves this shape for agent
    frontmatter with a line-anchored `count=1` replace; the same technique applies.
    """
    if key.split(".")[0] in _NORMATIVE:
        raise ValueError(
            f"refusing to write {key!r}: only {_REPAIRABLE[0]} is agent-maintained. "
            f"{', '.join(_NORMATIVE)} are the owner's — an agent able to edit those "
            f"could disable the gate it is about to face."
        )
    p = path or PROJECT_FILE
    text = p.read_text()
    stamp = f"  # auto-repaired {date.today().isoformat()}"

    span = _stack_entry_span(text, stack)
    if span:
        head, body, tail = text[: span[0]], text[span[0] : span[1]], text[span[1] :]
        body = _set_in_entry(body, key, command, stamp)
        if body is None:
            return False  # already correct: idempotent, writes nothing
        p.write_text(head + body + tail)
        return True

    # The stack is named as a bare string; promote it to a mapping so it can
    # carry an override. `_load_stack` already accepts either form.
    bare = re.compile(rf"^([ \t]*)-[ \t]*{re.escape(stack)}[ \t]*(?:#.*)?$", re.M)
    m = bare.search(text)
    if not m:
        raise ValueError(
            f"{stack!r} is not listed under `stacks:` in {p} — cannot record a repair"
        )
    pad = m.group(1)
    block = f"{pad}- name: {stack}\n{pad}  commands:\n{pad}    {key}: {command}{stamp}"
    p.write_text(bare.sub(block, text, count=1))
    return True


# --- the ladder ---------------------------------------------------------------


def _first_working_candidate(
    stack: Stack, key: str, *, runner=None
) -> tuple[str | None, list[str]]:
    """Probe each candidate for `key`; return the first that passes, and all tried.

    THE CANDIDATE ITSELF IS RUN, not the stack's `verify`. `verify` proves the
    declared runner still works; it says nothing about a replacement command,
    because it does not reference one. Swapping a candidate in and re-running
    `verify` would prove only that `verify` is still `verify`.

    So a candidate is adopted on its own exit status, under the probe timeout — a
    replacement nobody can confirm quickly is not one to adopt unattended. This
    costs a real run, which is acceptable because it happens only when the config
    is already broken.
    """
    # A candidate must be at least as capable as the incumbent. Replacing a
    # `{path}` command with one that cannot take a path would leave every worker
    # running the WHOLE suite for a one-file change — green, slow, and silently
    # not what the config promised.
    scoped = "{path}" in (stack.commands.get(key) or "")
    tried: list[str] = []
    for cand in candidates(stack, key):
        if scoped and "{path}" not in cand:
            continue
        tried.append(cand)
        trial = replace(stack, commands={**stack.commands, key: cand})
        if run_key(trial, key, timeout=PROBE_TIMEOUT, runner=runner).status == OK:
            return cand, tried
    return None, tried


def _probe_reason(stack: Stack, runner=None) -> str | None:
    """Why this stack's commands look wrong, or None if they look fine.

    Rung 1 then rung 2, cheapest first. A missing executable is diagnosed here but
    is NOT a stopping condition: a command naming a binary that does not exist is
    precisely the rotted case repair is for. Only rung 4 stops.
    """
    declared = [k for k in PROBE_KEYS if resolve(stack, k)]
    tools = {tool_of(resolve(stack, k) or "") for k in declared}
    missing = sorted(t for t in tools if t and not shutil.which(t))
    if missing:
        return f"not on PATH: {', '.join(missing)}"

    out = run_key(stack, "verify", timeout=PROBE_TIMEOUT, runner=runner)
    return None if out.status == OK else (out.detail or out.status)


def _broken_key(stack: Stack) -> str:
    """The command key most in need of repair.

    A key whose executable is missing is broken beyond doubt, so those come first.
    Failing that, the probe told us the runner is wrong, and the scoped test
    command is the one workers actually invoke.
    """
    for k in PROBE_KEYS:
        cmd = stack.commands.get(k)
        if k != "verify" and cmd and not shutil.which(tool_of(cmd)):
            return k
    return "test_scoped" if stack.commands.get("test_scoped") else "test"


def probe_stack(stack: Stack, *, repair: bool = False, runner=None) -> list[Finding]:
    """The ladder for one stack: probe, repair if it can, block only if it cannot."""
    # Rung 2 — nothing to probe with. Report it rather than guessing: the
    # toolchain is present (rung 1 found its binaries), but nothing claims the
    # commands still work, and saying "ok" here would be a reassuring artefact
    # standing in for a measurement.
    if not resolve(stack, "verify"):
        declared = [k for k in PROBE_KEYS if resolve(stack, k)]
        tools = {tool_of(resolve(stack, k) or "") for k in declared}
        missing = sorted(t for t in tools if t and not shutil.which(t))
        if not missing:
            return [
                Finding(
                    stack.name,
                    "unproven",
                    "declares no `commands.verify`, so nothing cheap can prove its "
                    "commands still work. Add one (a collect-only or --version form).",
                )
            ]

    reason = _probe_reason(stack, runner=runner)
    if reason is None:
        return [Finding(stack.name, "ok", "verify clean")]

    # Rung 3 — repair the key that is ACTUALLY broken. Guessing the primary test
    # key instead once "fixed" `test_scoped` while leaving the broken `test` in
    # place — a repair that reports success and changes nothing that mattered.
    key = _broken_key(stack)
    was = stack.commands.get(key) or ""
    winner, tried = _first_working_candidate(stack, key, runner=runner)
    if winner and not repair:
        return [
            Finding(
                stack.name,
                "broken",
                f"{key} fails its probe ({reason}); `{winner}` would work. "
                f"Re-run with --repair to record it.",
            )
        ]
    if winner:
        write_repair(stack.name, key, winner)
        return [Finding(stack.name, "repaired", key=key, was=was, now=winner)]

    # Rung 4 — nothing works. The only stop.
    if tried:
        why = f"tried {len(tried)} candidate(s) from the repo, none worked"
    else:
        why = "no candidate found in package.json, the Makefile or CI"
    return [Finding(stack.name, "broken", f"`{was}` fails ({reason}); {why}")]


def check(
    *, repair: bool = False, project: Project | None = None, runner=None
) -> list[Finding]:
    p = project or load()
    out: list[Finding] = []
    for s in p.stacks:
        out += probe_stack(s, repair=repair, runner=runner)
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(
        prog="check-stack-commands.sh",
        description="Probe each stack's declared commands; repair what has rotted.",
    )
    ap.add_argument(
        "--repair",
        action="store_true",
        help="write a working command into harness.yaml when the declared one fails "
        "(read-only without it, so CI stays safe)",
    )
    args = ap.parse_args(argv)

    try:
        findings = check(repair=args.repair)
    except Exception as exc:  # noqa: BLE001 — a config error must not traceback here
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    if not findings:
        print("no stacks declared — nothing to probe.")
        return 0

    # NAME THE PROJECT. A check that answers about the wrong repository is the failure
    # this guards: it does not error, it reports a clean pass about somebody else. The
    # resolution is three fallbacks deep and invisible, so every check says whose
    # answer this is.
    from .project import load as _load

    print(f"project: {_load().name}")
    print("stack commands:")
    for f in findings:
        print(f.line())

    broken = [f for f in findings if f.status == "broken"]
    repaired = [f for f in findings if f.status == "repaired"]
    if repaired:
        print(
            f"\n{len(repaired)} command(s) repaired in {PROJECT_FILE.name}. "
            f"Review the diff before committing — a repair is a real config change."
        )
    if broken:
        print(f"\n{len(broken)} stack(s) have no working command.", file=sys.stderr)
        if not args.repair:
            print(
                "  Re-run with --repair to try candidates from the repo.",
                file=sys.stderr,
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
