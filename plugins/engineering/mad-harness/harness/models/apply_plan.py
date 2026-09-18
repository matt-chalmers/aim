"""Apply a planner's plan to the tracker in one call — campaign-loop §3e and §3f as a script.

MEASURED. A field campaign orchestrator ran 237 requests with its context growing from
55k to 920k tokens, averaging ~380k, so every tool call it makes re-reads ~380k tokens of
cache — about $0.11-0.17 per call, roughly 6x what the same call costs a worker. That
context was 35% its own outputs, 35% injected text (subagent results, landing in full and
again as a task notification) and 7% tool results. §3e — "run the create / update / dep
lines one at a time, echoing each id" — was 10-30 of those calls per epic, every one of
them deterministic: the planner had already decided everything, and the orchestrator was
paying its highest context to act as a shell. The levers at the top of the tree are fewer
turns and artefacts passed by PATH rather than by content; this is both. Source:
TipDonkey `.harness/cost_control_orchestration.md` §O5 and §6 (A4).

THE LABEL CONVENTION, which is what lets a plan be applied without a model in the loop.
A plan's `create` lines do not know the ids the tracker will hand out, so the planner
names each one — `T1: tk.sh create "…"` — and later lines reference the label as a bare
token where an id would go: `tk.sh dep T2 T1`, `--parent T1`, `gate create T3`. The
contract is stated once, in `agents/planner.md` (output contract, item 4); this module
reads it. The `${CLAUDE_PLUGIN_ROOT}` prefix a planner writes is substituted with the real
plugin root, so the plan file is runnable text wherever the plugin is installed.

VALIDATE THE WHOLE PLAN, THEN WRITE. Every line is checked before the first command runs —
a label that is never created, a label used before its create, a non-`tk.sh` command, the
forms `campaign-loop` already bans (`create --graph`, `prime`, positional `close <id>
"msg"`), a line the real CLI cannot parse — and one refusal means NOTHING is written. A
plan applied halfway by a model is exactly the state this replaces: an epic with some of
its tasks, some of its edges, and a context that has to work out which.

RESUMABLE BY LABEL, NOT BY LINE. The label → id map is written to
`<repo>/.harness/run/apply-plan-<epic>.json` after every create, so a rerun after a
failure skips the labels already created and carries on. Keyed by label because the plan
is what gets edited between runs: a fix to the failing line shifts every line number and
none of the labels. A reused label whose create carries a different title is refused —
that is a revised plan colliding with an earlier apply, not a resume. Every other line is
recorded by a fingerprint of its text once it has run, and skipped on a rerun for the same
reason: a `close` that ran once fails the second time against a compacted record, and a
`note` that ran once appends twice. The file is never deleted by this tool; deleting it is
how an operator says "this epic is being planned again from nothing".
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .resolve import PLUGIN_ROOT

TK = PLUGIN_ROOT / "harness" / "tracker" / "tk.sh"
RENDER = PLUGIN_ROOT / "harness" / "tracker" / "render-epic.sh"

#: `T1: ` — a label is an identifier, then a colon and whitespace, at the start of a line.
LABEL = re.compile(r"^(?P<label>[A-Za-z][\w-]*):\s+(?P<rest>\S.*)$", re.DOTALL)
#: Fence info strings whose contents are commands. A plan's DAG tree and matrix sit in
#: plain fences and are never mistaken for lines to run.
BASH_FENCE = re.compile(r"^\s*```\s*(bash|sh|shell)\b", re.IGNORECASE)
FENCE = re.compile(r"^\s*```")

#: The verbs a plan may carry — the ones that write records. A read in a plan does
#: nothing; a coordination verb (claim, slot-*) is a wave's business, not a plan's.
PLAN_VERBS = frozenset({"create", "dep", "update", "note", "close", "supersede", "delete", "label", "gate"})
#: Per verb, the parsed attributes that hold a record id — where a label may stand.
ID_FIELDS: dict[str, tuple[str, ...]] = {
    "create": ("parent",),
    "dep": ("dependent", "blocker"),
    "update": ("id", "parent"),
    "note": ("id",),
    "close": ("id",),
    "supersede": ("old", "new"),
    "delete": ("id",),
    "label": ("id",),
    "gate": ("target",),
}
#: Leading options `tk.sh` accepts before the verb, and whether each takes a value.
GLOBAL_FLAGS = {"--json": False, "--readonly": False, "--backend": True}

#: One subprocess of the real CLI. Generous: a beads backend forks its own binary.
TIMEOUT = 120


@dataclass(frozen=True)
class Command:
    """One `tk.sh` line of the plan, labels still symbolic."""

    line: int
    label: str | None
    argv: tuple[str, ...]  # after `tk.sh`: verb and arguments, exactly as the plan wrote them
    verb: str
    refs: tuple[str, ...]  # every id-bearing value, symbolic
    title: str = ""  # a create's title, for the report and the collision check

    @property
    def creates(self) -> bool:
        """Whether the CLI prints a new id for this line — `create`, or `gate create`."""
        return self.verb == "create" or (self.verb == "gate" and self.argv[1:2] == ("create",))

    def resolved(self, labels: dict[str, str]) -> list[str]:
        """The argv to run, every label replaced by its id."""
        out = []
        for tok in self.argv:
            if tok in labels:
                out.append(labels[tok])
            elif "=" in tok and tok.startswith("-") and tok.split("=", 1)[1] in labels:
                opt, _, val = tok.partition("=")
                out.append(f"{opt}={labels[val]}")
            else:
                out.append(tok)
        return out


def fingerprint(argv: tuple[str, ...]) -> str:
    """A line's identity across reruns: its text with labels still symbolic, so a fixed
    line is a new line and an untouched one is the same line wherever it moved to."""
    return hashlib.sha1(shlex.join(argv).encode()).hexdigest()[:16]


@dataclass
class State:
    """What an earlier apply of this epic did, persisted after every command so a rerun
    resumes rather than repeats."""

    epic: str
    path: Path
    plan: str = ""
    labels: dict[str, dict[str, str]] = field(default_factory=dict)
    applied: list[str] = field(default_factory=list)

    @property
    def rerun(self) -> bool:
        return bool(self.labels or self.applied)

    @classmethod
    def load(cls, epic: str) -> State:
        from tracker.locks import run_dir
        from tracker.port import TrackerError

        from .resolve import repo_root

        # The shared run dir — the PRIMARY checkout's, so an apply started from a
        # worktree and resumed from the primary find the same file.
        try:
            base = run_dir()
        except TrackerError:
            base = repo_root() / ".harness" / "run"
        path = base / f"apply-plan-{epic}.json"
        state = cls(epic=epic, path=path)
        try:
            data = json.loads(path.read_text())
            state.labels = dict(data.get("labels") or {})
            state.applied = list(data.get("applied") or [])
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
        return state

    def ids(self) -> dict[str, str]:
        return {k: v["id"] for k, v in self.labels.items() if v.get("id")}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "epic": self.epic,
            "plan": self.plan,
            "labels": self.labels,
            "applied": self.applied,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }, indent=2) + "\n")

    def record(self, label: str, task_id: str, title: str) -> None:
        self.labels[label] = {"id": task_id, "title": title}
        self.save()

    def mark(self, fp: str) -> None:
        self.applied.append(fp)
        self.save()


# --- parsing -----------------------------------------------------------------------


def bash_blocks(text: str) -> list[tuple[int, list[str]]]:
    """(first line number, lines) of every fenced bash block."""
    blocks: list[tuple[int, list[str]]] = []
    current: list[str] | None = None
    start = 0
    for n, line in enumerate(text.splitlines(), 1):
        if current is None:
            if BASH_FENCE.match(line):
                current, start = [], n + 1
            continue
        if FENCE.match(line):
            blocks.append((start, current))
            current = None
            continue
        current.append(line)
    if current is not None:
        blocks.append((start, current))  # an unclosed fence still holds its commands
    return blocks


def logical_lines(start: int, lines: list[str]) -> list[tuple[int, str, list[str] | None]]:
    """(line, text, argv) for every command in a block, with `\\` continuations joined and
    a quoted string allowed to span lines. argv is None when the quotes never balance."""
    out: list[tuple[int, str, list[str] | None]] = []
    buf: list[str] = []
    first = start
    for n, raw in enumerate(lines, start):
        line = raw.rstrip()
        if not buf:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            first = n
        if line.endswith("\\"):
            buf.append(line[:-1])
            continue
        buf.append(line)
        text = "\n".join(buf)
        try:
            argv = shlex.split(text, comments=True)
        except ValueError:
            continue  # a quote is still open; keep accumulating
        out.append((first, text, argv))
        buf = []
    if buf:
        out.append((first, "\n".join(buf), None))
    return out


def _is_tk(token: str) -> bool:
    """`tk.sh` by any path — `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh`, an absolute
    path, or bare. The real wrapper is what runs, whatever prefix the plan wrote."""
    return Path(token).name == "tk.sh" and ":" not in token


def _verb_of(rest: list[str]) -> str | None:
    """The verb, past any leading global flag."""
    i = 0
    while i < len(rest) and rest[i] in GLOBAL_FLAGS:
        i += 2 if GLOBAL_FLAGS[rest[i]] else 1
    return rest[i] if i < len(rest) else None


def _parse_cli(rest: list[str]):
    """Parse against the real CLI. (namespace, "") or (None, why)."""
    from tracker.cli import build_parser

    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            return build_parser().parse_args(rest), ""
    except SystemExit:
        lines = [ln for ln in err.getvalue().splitlines() if ln.strip()]
        return None, (lines[-1].split("error: ", 1)[-1] if lines else "does not parse")


def parse(text: str) -> tuple[list[Command], list[str], set[str]]:
    """Every command in the plan, every reason it cannot be applied as written, and every
    label that heads a line — including lines refused here, so a reference to one is not
    reported a second time as unknown."""
    commands: list[Command] = []
    problems: list[str] = []
    labels: set[str] = set()
    for start, lines in bash_blocks(text):
        for n, raw, argv in logical_lines(start, lines):
            where = f"line {n}"
            m = LABEL.match(raw)
            label = m.group("label") if m else None
            if label:
                labels.add(label)
                try:
                    argv = shlex.split(m.group("rest"), comments=True)
                except ValueError:
                    argv = None
            if argv is None:
                problems.append(f"{where}: quotes never balance: {raw.splitlines()[0][:80]}")
                continue
            if not argv:
                continue
            if not _is_tk(argv[0]):
                if re.match(r"^[A-Za-z][\w-]*:\S*tk\.sh$", argv[0]):
                    problems.append(f"{where}: a label needs a space after its colon: `{argv[0][:40]}`")
                else:
                    problems.append(f"{where}: not a tk.sh command: `{argv[0]}` — a plan block holds tk.sh lines only")
                continue
            rest = argv[1:]
            verb = _verb_of(rest)
            if "--readonly" in rest:
                problems.append(f"{where}: `--readonly` on a plan line — every plan line writes")
                continue
            if verb == "prime":
                problems.append(f"{where}: `prime` — the planner is told never to run it, and a plan applies records")
                continue
            if verb == "create" and any(t == "--graph" or t.startswith("--graph=") for t in rest):
                problems.append(f"{where}: `create --graph` — its --dry-run is silently ignored; one create per line")
                continue
            if verb == "close" and not any(t == "--reason" or t.startswith("--reason=") for t in rest):
                problems.append(f"{where}: positional `close <id> \"msg\"` — write `close <id> --reason \"…\"`")
                continue
            if verb not in PLAN_VERBS:
                problems.append(f"{where}: `{verb}` is not a plan verb; a plan carries {', '.join(sorted(PLAN_VERBS))}")
                continue
            ns, why = _parse_cli(rest)
            if ns is None:
                problems.append(f"{where}: `tk.sh {' '.join(rest[:3])}…` does not parse: {why}")
                continue
            refs = tuple(str(getattr(ns, f)) for f in ID_FIELDS[verb] if getattr(ns, f, None))
            cmd = Command(
                line=n, label=label, argv=tuple(rest), verb=verb, refs=refs,
                title=str(getattr(ns, "title", "") or getattr(ns, "reason", "") or ""),
            )
            if label and not cmd.creates:
                problems.append(f"{where}: label `{label}` on a `{verb}` line — only `create` and `gate create` print an id")
                continue
            commands.append(cmd)
    return commands, problems, labels


# --- validation --------------------------------------------------------------------


class Tracker:
    """The real CLI, one subprocess per call. Lookups are cached: a plan names its epic
    on every create, and one `show` answers all of them."""

    def __init__(self, tk: Path = TK):
        self.tk = tk
        self._known: dict[str, bool] = {}

    def run(self, *argv: str) -> subprocess.CompletedProcess:
        cmd = [str(self.tk), *argv]
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT, env=os.environ)
        except subprocess.TimeoutExpired:
            # A hang is its own outcome, never a pass — the recorded multi-hour stall was
            # a command blocking on a prompt nobody saw.
            return subprocess.CompletedProcess(cmd, 124, "", f"no answer within {TIMEOUT}s")

    def exists(self, task_id: str) -> bool:
        if task_id not in self._known:
            self._known[task_id] = self.run("show", task_id).returncode == 0
        return self._known[task_id]


def validate(
    commands: list[Command], epic: str, state: State, tracker: Tracker, labels: set[str] | None = None
) -> list[str]:
    """Every reason not to run this plan, or an empty list. Reads the tracker; writes nothing."""
    problems: list[str] = []
    if not tracker.exists(epic):
        problems.append(f"epic {epic} is not a record the tracker knows")
    all_labels = [c.label for c in commands if c.label]
    dupes = sorted({x for x in all_labels if all_labels.count(x) > 1})
    for d in dupes:
        problems.append(f"label `{d}` is created more than once — every label names one record")
    defined = set(state.ids())
    plan_labels = set(all_labels) | (labels or set())
    for c in commands:
        where = f"line {c.line}"
        for ref in c.refs:
            if ref in defined:
                continue
            if ref in plan_labels:
                problems.append(f"{where}: `{ref}` is used before its create — order the plan so a create precedes every reference")
            elif not tracker.exists(ref):
                problems.append(f"{where}: `{ref}` is not a label this plan creates and not a record the tracker knows")
        if c.verb == "delete" and c.refs and c.refs[0] == epic:
            problems.append(f"{where}: `delete {epic}` — never delete the epic")
        if c.creates:
            if not c.label and state.rerun:
                problems.append(
                    f"{where}: an unlabelled create on a rerun — {state.path} records an earlier apply, and an "
                    f"unlabelled create cannot be told from one already applied. Label it, or delete that file to start over."
                )
            prior = state.labels.get(c.label or "")
            if prior and prior.get("title", c.title) != c.title:
                problems.append(
                    f"{where}: `{c.label}` was created as {prior['id']} titled {prior['title']!r} by an earlier apply; "
                    f"this plan titles it {c.title!r}. A revised plan reusing a label is not a resume — "
                    f"relabel it, or delete {state.path} to start over."
                )
        if c.label:
            defined.add(c.label)
    return problems


# --- application -------------------------------------------------------------------


def _describe(c: Command, argv: list[str], new_id: str | None, labels: dict[str, str]) -> str:
    refs = [labels.get(r, r) for r in c.refs]
    if c.verb == "create":
        head = f"{c.label} -> {new_id}" if c.label else f"{new_id}"
        return f'{head}  created "{c.title}"'
    if c.verb == "gate" and new_id:
        head = f"{c.label} -> {new_id}" if c.label else f"{new_id}"
        return f"{head}  gate created on {refs[0] if refs else '?'}"
    if c.verb == "dep":
        return f"dep {refs[0]} -> {refs[1]}"
    if c.verb == "supersede":
        return f"supersede {refs[0]} -> {refs[1]}"
    if c.verb in ("label", "gate"):
        return f"{c.verb} {' '.join(argv[1:])}"
    return f"{c.verb} {' '.join(refs)}"


def apply(commands: list[Command], state: State, plan: Path, tracker: Tracker, out=None) -> tuple[int, dict[str, int], str]:
    """Run the plan in order. Returns (commands applied, counts, failure or '')."""
    out = out or sys.stdout
    counts = {"created": 0, "already": 0, "deps": 0, "other": 0}
    applied = 0
    state.plan = str(plan)
    # What an EARLIER run did. A line is skipped against this, never against what this
    # run has just done — two identical lines in one plan both run.
    done_before = set(state.applied)
    for c in commands:
        labels = state.ids()
        if c.creates and c.label and c.label in labels:
            print(f"{c.label} = {labels[c.label]}  (already created)", file=out)
            counts["already"] += 1
            applied += 1
            continue
        if not c.creates and fingerprint(c.argv) in done_before:
            print(f"{_describe(c, c.resolved(labels), None, labels)}  (already applied)", file=out)
            counts["already"] += 1
            applied += 1
            continue
        argv = c.resolved(labels)
        proc = tracker.run(*argv)
        if proc.returncode != 0:
            detail = (proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}").splitlines()[-1]
            return applied, counts, f"line {c.line}: tk.sh {shlex.join(argv)}\n  {detail}"
        new_id = None
        if c.creates:
            new_id = (proc.stdout.strip().splitlines() or [""])[-1].strip()
            if not new_id:
                return applied, counts, f"line {c.line}: tk.sh {shlex.join(argv)} printed no id"
            if c.label:
                state.record(c.label, new_id, c.title)
            counts["created"] += 1
        else:
            counts["deps" if c.verb == "dep" else "other"] += 1
            state.mark(fingerprint(c.argv))
        applied += 1
        print(_describe(c, argv, new_id, labels), file=out)
    return applied, counts, ""


def dry_run(commands: list[Command], state: State, out=None) -> None:
    out = out or sys.stdout
    known = state.ids()
    for c in commands:
        mark = f"[skip: {c.label} = {known[c.label]}] " if c.creates and c.label and c.label in known else ""
        head = f"{c.label}: " if c.label else ""
        print(f"{mark}{head}tk.sh {shlex.join(c.argv)}", file=out)


def _caller_path(p: str) -> Path:
    """A path the caller gave, resolved where the caller stood — the wrapper `cd`s into the
    harness before Python starts, so a relative `plan.md` would otherwise be looked for there."""
    path = Path(p)
    if path.is_absolute():
        return path
    caller = os.environ.get("MAD_HARNESS_CALLER_PWD")
    return (Path(caller) if caller else Path.cwd()) / path


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="apply-plan.sh",
        description="Apply a planner's plan to the tracker: every tk.sh line in its bash blocks, labels resolved to ids.",
    )
    ap.add_argument("plan", help="the planner's output, markdown")
    ap.add_argument("--epic", required=True, help="the epic the plan belongs to")
    ap.add_argument("--dry-run", action="store_true", help="print every command with labels symbolic; run nothing")
    ap.add_argument("--render", metavar="PATH", help="after a successful apply, write the epic's view here")
    args = ap.parse_args(argv)

    plan = _caller_path(args.plan)
    try:
        text = plan.read_text()
    except OSError as exc:
        print(f"cannot read plan: {exc}", file=sys.stderr)
        return 2

    if not args.epic.strip():
        print("--epic needs an id", file=sys.stderr)
        return 2
    commands, problems, labels = parse(text)
    if not commands and not problems:
        print(f"REFUSED: no tk.sh command in any ```bash block of {plan}", file=sys.stderr)
        return 2
    state = State.load(args.epic)
    tracker = Tracker()
    problems += validate(commands, args.epic, state, tracker, labels)
    if problems:
        print(f"REFUSED — {len(problems)} problem(s); nothing written:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2

    if args.dry_run:
        dry_run(commands, state)
        print(f"dry-run: {len(commands)} commands, nothing written")
        return 0

    applied, counts, failure = apply(commands, state, plan, tracker)
    # The per-command lines went to stdout as they ran; the verdicts below go to stderr.
    # Piped, stdout is block-buffered and a verdict would print ABOVE the lines it judges.
    sys.stdout.flush()
    if failure:
        print(f"FAILED at {failure}", file=sys.stderr)
        print(
            f"applied {applied} of {len(commands)}; {counts['created']} created, {counts['already']} already "
            f"applied. Fix the line and rerun — what was applied is skipped.",
            file=sys.stderr,
        )
        return 1

    summary = (
        f"applied {applied} commands: {counts['created']} created, {counts['already']} already applied, "
        f"{counts['deps']} deps, {counts['other']} other"
    )
    # §3f, in the same call: the DAG check, and the view. The records are written by now,
    # so a failure here still reports what was applied.
    val = tracker.run("validate", args.epic)
    if val.returncode != 0:
        print(summary)
        print(val.stdout.rstrip())
        sys.stdout.flush()
        print(f"validate {args.epic}: NOT OK{(' — ' + val.stderr.strip()) if val.stderr.strip() else ''}", file=sys.stderr)
        return 1
    try:
        v = json.loads(val.stdout)
        waves = v.get("waves") or []
        width = max((len(w.get("task_ids") or ()) for w in waves), default=0)
        shape = f"{len(waves)} waves, max parallelism {width} (dependency-only; ignores file contention)"
    except (json.JSONDecodeError, AttributeError):
        shape = "ok"
    summary += f"; validate {args.epic}: {shape}"
    if args.render:
        r = subprocess.run(
            [str(RENDER), args.epic, "--write", args.render],
            capture_output=True, text=True, timeout=TIMEOUT, env=os.environ,
        )
        if r.returncode != 0:
            print(summary)
            print((r.stderr or r.stdout).rstrip(), file=sys.stderr)
            print(f"render-epic {args.epic} --write {args.render}: FAILED", file=sys.stderr)
            return 1
        summary += f"; rendered {args.render}"
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
