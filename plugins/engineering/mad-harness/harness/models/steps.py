"""One step of a collapsed loop sequence: run it, bound it, digest it to a line.

WHY A SEQUENCE IS WORTH COLLAPSING. A field campaign orchestrator ran 237 requests with
its context growing 55k -> 920k tokens, ~380k on average, so every tool call it makes
re-reads ~380k tokens of context — ~$0.11-0.17 per call, about 6x what the same call
costs a worker. Its context was 35% its own outputs, 35% injected results, 7% tool
results: the lever at the top is FEWER TURNS, and a mechanical sequence the loop spells
out as six or eight shell lines is six or eight turns that one script does in one.

Two modules share this: `preflight.py` (campaign-loop §0) and `close_epic.py` (§5).
What they share is not the steps but the discipline around each step:

  * **A hang is its own outcome, never a pass.** The recorded 8.5-hour stall was a
    command blocking on a permission prompt in another session. Every step here has a
    timeout and reports it as a failure with the step's name.
  * **The output is digested, not forwarded.** A tool result is paid on every later
    turn. The line carries a verdict and the last few lines that explain it; the
    whole output goes nowhere, because the checks already print their own digests.
  * **The runner is injected**, so the tests drive the sequences with no tracker, no
    git remote and no config check behind them — the SEQUENCE is what is under test.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

OK = "ok"
FAIL = "FAIL"
UPGR = "UPGR"
INFO = "info"
#: Not run, because a step before it failed. Distinct from FAIL: nothing was measured.
SKIP = "skip"

MARK = {OK: "ok  ", FAIL: "FAIL", UPGR: "UPGR", INFO: "info", SKIP: "skip"}

#: A step that has produced nothing in this long is stuck, not slow. The checks measure
#: in seconds; git against a remote is the only step that legitimately waits.
DEFAULT_TIMEOUT = 120
REMOTE_TIMEOUT = 300

#: How much of a step's output survives into the report.
TAIL_LINES = 4
LINE_WIDTH = 200


@dataclass(frozen=True)
class Raw:
    """What running a step produced, before anyone judged it."""

    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    #: Set when the step never returned an exit status: a hang, or no such executable.
    error: str = ""

    @property
    def ran(self) -> bool:
        return self.returncode is not None


@dataclass
class Result:
    """One line of the report. `detail` may span lines; the renderer indents them."""

    name: str
    status: str
    detail: str = ""
    raw: Raw | None = None
    #: What to do by hand when this step failed and stopped the sequence.
    remaining: list[str] = field(default_factory=list)

    def line(self, width: int = 34) -> str:
        head = f"[{MARK[self.status]}] {self.name:<{width}} "
        pad = "\n" + " " * len(head)
        return (head + self.detail.replace("\n", pad)).rstrip()


def execute(
    argv: list[str],
    *,
    cwd: str,
    timeout: int = DEFAULT_TIMEOUT,
    runner=None,
) -> Raw:
    """Run one command and report what happened. Never raises.

    :param runner: injected for tests; defaults to :func:`subprocess.run` and must
        accept its keyword form (`argv, cwd=, capture_output=, text=, timeout=`).
    """
    run = runner or subprocess.run
    try:
        proc = run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return Raw(
            None,
            error=(
                f"no output within {timeout}s. A step that hangs is usually blocking on a "
                f"prompt in another session — nothing here passed."
            ),
        )
    except OSError as exc:
        return Raw(None, error=str(exc)[:LINE_WIDTH])
    return Raw(proc.returncode, proc.stdout or "", proc.stderr or "")


def tail(text: str, n: int = TAIL_LINES) -> str:
    """The last `n` non-empty lines, each cut to the report's width."""
    lines = [ln.rstrip()[:LINE_WIDTH] for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines[-n:]) if lines else ""


def failure_detail(raw: Raw) -> str:
    """Why a step failed, in a few lines: stderr first, stdout when that is empty."""
    if raw.error:
        return raw.error
    why = tail(raw.stderr) or tail(raw.stdout)
    return f"exit {raw.returncode}" + (f"\n{why}" if why else "")


def render(results: list[Result]) -> str:
    width = max((len(r.name) for r in results), default=0)
    return "\n".join(r.line(width) for r in results)
