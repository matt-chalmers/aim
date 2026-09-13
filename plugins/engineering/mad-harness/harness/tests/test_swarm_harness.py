"""Swarm harness: the two invariants that a stalled or corrupted campaign turns on.

This is the invariant's third recurrence. The first fix was prose; the second
rewrote five files; it came back anyway, because both were documentation and the
*script* every worker is told to run still contradicted them. Prose cannot hold
an invariant that costs eight and a half hours when it breaks.

The two failures, which are different and both silent:

* **The isolating variable was never exported.** ``.swarm-env`` carried a name the
  test runner did not read. A worker that followed the doctrine exactly — source the
  env, then run the suite — fell through to the shared default. Parallel workers then
  *share* a database and corrupt each other's fixtures, which is worse than the stall
  it replaced: the suite still goes green, so nothing announces it.
* **The banned inline form.** ``VAR=value <runner> …`` starts with ``VAR=``, not the
  runner, so a ``Bash(<runner>:*)`` prefix permission rule cannot match it. The call
  stops on a permission prompt that surfaces in the *orchestrator's* session; in an
  unattended run nobody is there, and the dispatch hangs indefinitely.

Two decisions worth recording, so the next reader does not re-litigate them:

* The per-worker-env assertion reads the generated block rather than
  executing it. Running the real script creates a **git worktree** — persistent
  repo state — and a test that leaves worktrees behind when it fails is worse
  than the bug it guards. The heredoc is a literal block, so parsing it asserts
  exactly what the script would write, minus shell expansion of ``${N}``.
* The banned-form pattern is anchored at line start (allowing indentation),
  which is what lets ``test-doctrine`` keep *quoting* the inline form in order
  to forbid it. That documentation line begins with a backtick, so it is not a
  false positive — and the anchor is load-bearing, not incidental.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INIT_SCRIPT = REPO / "harness" / "swarm" / "swarm-worktree-init.sh"

# What actually stalls is a command string that BEGINS with an env assignment,
# because a `Bash(<runner>:*)` prefix rule can then never match it. Two ways a repo
# file causes that, and the guard needs both — the first draft of this test had only
# the first and missed the real historical bug:
#
#   RUNS it    — `VAR=x <runner> …` at the start of a line.
#   ADVISES it — `echo "    VAR=x <runner> …"`, which is what the worktree bootstrap
#                actually did. The LINE starts with `echo`, so a line-anchored
#                pattern sails straight past.
#
# Which VAR and which runner is a property of a toolchain, so the patterns come from
# `stack.banned_forms` rather than being named here.
#
# Deliberately NOT flagged, because neither can stall:
#   * a form wrapped in a subshell — `(cd <dir> && VAR=… <runner> …)`. The command
#     string starts with `(`, so no prefix rule is being defeated.
#   * comments, and markdown that quotes the form in order to forbid it, which
#     is exactly what test-doctrine is for.
#: A script printing an inline-env command as advice is as harmful as running it:
#: the reader copies it. Kept generic — the runner comes from the stack pattern.
ADVISES = re.compile(r"""echo\s+["']\s*[A-Z_]+=\S*\s+\S""")
SHELL_COMMENT = re.compile(r"^\s*#")

# Markdown may quote the banned form to ban it; a shell script may not print it.
DOC_SUFFIXES = {".md"}


def _tracked_files(*prefixes: str) -> list[Path]:
    """Git-tracked paths under the given prefixes. Untracked scratch is not our problem."""
    out = subprocess.run(
        ["git", "ls-files", "--", *prefixes],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [REPO / line for line in out.splitlines() if line]


def _env_block() -> str:
    """Exactly what `.swarm-env` will contain, without creating a worktree.

    This used to parse a heredoc out of the shell script, because running the real
    script creates a git worktree — persistent repo state that a failing test would
    leave behind, which is worse than the bug it guards. The values now come from
    the stack modules via `worker.env_block`, which is a PURE FUNCTION: it can be
    called directly, so the guard reads the actual source of truth rather than a
    literal that merely resembles it.
    """
    from models.worker import env_block

    return env_block(3, "backend", Path("/main"))


def test_the_generated_env_gives_each_worker_its_own_resources() -> None:
    """Without per-worker isolation, parallel workers share state and corrupt each
    other — silently, because each one's suite still passes.

    Stated generically: at least one exported variable must be parameterised by the
    worker number. Which variable that is (a database, a build directory, a cache)
    is the project's business, declared by its stack modules.
    """
    from models.project import load

    stack_env = load().worker_env(1)
    assert stack_env, (
        "no stack declares any per-worker environment — every worker would share "
        "every resource. Add an `env:` entry to the stack module that owns one."
    )
    a, b = load().worker_env(1), load().worker_env(2)
    differing = [k for k in a if a[k] != b.get(k)]
    assert differing, f"no variable differs between workers 1 and 2: {a}"


def test_the_banned_form_sweep_actually_examines_files() -> None:
    """A sweep over directories that do not exist reports clean having read nothing.

    The prompt tree was `.claude/` and `scripts/` before extraction; both are gone, so
    the sweep below silently covered only `harness/` — none of the prose that actually
    tells a worker what to run. An empty file set must fail, not pass.
    """
    files = _tracked_files("harness", "agents", "commands", "skills")
    assert len(files) > 50, (
        f"the banned-form sweep found only {len(files)} files — it is examining the wrong "
        f"directories and would report clean without reading the prompts"
    )


def test_no_tracked_file_prints_or_runs_a_banned_inline_form() -> None:
    """The patterns come from the STACK MODULES, not from this file.

    An inline `VAR=value <runner> …` form cannot match a `Bash(<runner>:*)` permission
    rule, so the call stops on a prompt that surfaces in the orchestrator's session —
    the recorded multi-hour stall, three times over.

    Which VAR and which runner is a property of a toolchain, so it belongs to the stack
    module that owns it. Until now `banned_forms` was parsed into `Stack.banned_forms`
    and read by nothing, while this test carried its own hardcoded copy of one stack's
    pattern — two statements of one rule, the config one inert. A project isolating
    workers by a different variable got no protection against the failure the harness
    calls its most expensive.

    That is the exact defect `frameworks/_template.yaml` names about its own removed
    `detect` key: a field that looks load-bearing while being inert is worse than its
    absence.
    """
    from models.project import load

    patterns: list[tuple[str, str, re.Pattern[str]]] = []
    for st in load().stacks:
        for form in st.banned_forms:
            pat = form.get("pattern")
            if pat:
                patterns.append((st.name, form.get("why", ""), re.compile(pat)))
    unguarded = [st.name for st in load().stacks if st.env and not st.banned_forms]
    assert not unguarded, (
        f"{unguarded} isolate workers through the environment but declare no "
        f"`banned_forms`. That is precisely the stack whose inline `VAR=value <runner>` "
        f"form stalls on a permission prompt, so it is precisely the stack that needs "
        f"the pattern declared."
    )

    offenders: list[str] = []
    for path in _tracked_files("harness", "agents", "commands", "skills"):
        if not path.is_file() or path.suffix in DOC_SUFFIXES:
            continue  # a doc quoting the form to ban it is the point, not a defect
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if SHELL_COMMENT.match(line):
                continue
            for stack, _why, rx in patterns:
                # Either running the form, or printing it as advice.
                if rx.match(line) or ADVISES.search(line):
                    offenders.append(
                        f"{path.relative_to(REPO)}:{lineno} [{stack}]: {line.strip()}"
                    )
                    break

    assert not offenders, (
        "these lines use a form the stack modules ban — it matches no prefix permission "
        "rule and would stall on a prompt nobody sees:\n  " + "\n  ".join(offenders)
    )


def test_every_python_wrapper_forwards_its_arguments() -> None:
    """A wrapper that execs a module without "$@" silently drops every flag.

    check-model-config.sh shipped this way for one commit: `--write` never reached
    Python, so `make models-sync` looked like it ran, printed the agent table, and
    changed nothing. Nothing failed — the sync just did not happen, which is the
    worst shape of bug for a tool whose job is keeping two files in agreement.
    """
    offenders: list[str] = []
    for path in _tracked_files("harness"):
        if path.suffix != ".sh" or not path.is_file():
            continue
        text = path.read_text()
        for line in text.splitlines():
            if "python -m " not in line:
                continue
            if '"$@"' not in line:
                offenders.append(f"{path.relative_to(REPO)}: {line.strip()}")

    assert not offenders, (
        'these wrappers exec a Python module without forwarding "$@", so every '
        "argument is silently discarded:\n  " + "\n  ".join(offenders)
    )
