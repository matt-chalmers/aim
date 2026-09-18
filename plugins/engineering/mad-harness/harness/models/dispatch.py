"""Invoke one agent through the CLI boundary, and record what it cost.

WHY A BOUNDARY AT ALL. Claude Code's own Agent tool takes its model from agent
frontmatter, which is fine until you want a tier per *task* rather than per agent, an
external provider, a hard spend ceiling, or the real token count. None of those are
expressible through it.

WHY THE AGENT SDK RATHER THAN ARGV WE BUILD OURSELVES. The SDK spawns the same Claude
Code CLI this module used to spawn by hand, so the capability is identical — but every
dispatch defect this harness has had lived in the hand-built argv rather than in the
agent: `--allowedTools` is variadic and swallowed the prompt; the grants had to be one
joined string rather than several; `--output-format` had to come last purely to
terminate the variadic flag; a `Read(...)` grant looked correct and did nothing where
`--add-dir` was required. Each surfaced as a plausible-looking result rather than an
error. None of them are expressible through a typed options object.

It also pins the CLI. The SDK ships its own, so a dispatch no longer runs against
whatever version happens to be installed and auto-updating on the machine.

WHY THIS IS A DROP-IN. swarm.md already states the dispatch contract: "The
Agent-tool prompt string is the only parent->child channel. The worker sees none
of this conversation." A worker is therefore already a self-contained prompt plus
a worktree plus tasks — which is exactly what a subprocess gets. The verification
lenses read the diff and the repo, never the worker's process, so they cannot
tell the difference and do not need to.

WHAT WE DO NOT PASS, AND WHAT WE MUST.

  --tools.  Still never. Measured, not assumed: dispatching `verifier` with
  `--tools ""` produced an agent that did not know it was lens 1 of 3, because
  the agent declares `skills: [test-doctrine]` and an empty tool set breaks skill
  loading. The same probe against `spec-editor` (no skills) looked fine, which is
  how this would have shipped unnoticed. The agent's own frontmatter already
  carries `tools:` and `disallowedTools:` and Claude Code honours them; overriding
  from out here can only desynchronise the two.

  --allowedTools / --permission-mode / --add-dir.  ALWAYS, and this paragraph
  used to say the opposite. The measurement above is about `--tools`, which
  selects the TOOL SET; it was generalised to `--allowedTools`, which carries
  PERMISSION RULES, and the two are different flags doing different jobs. That
  single conflation is why the harness shipped with no grants at all — writers
  could never commit through this boundary, and the lenses silently judged
  without being able to read their own briefs.

  The reason it needs saying at all is that this boundary CHANGED THE EXECUTION
  MODEL. Under the Agent tool a subagent runs inside the parent session and
  inherits its permission context, with a human available to approve anything
  else; permissions were ambient and nobody had to think about them. Headless
  `-p` has no approver, so a permission it does not carry is a permission it
  does not have. Nothing new was invented here: what the Agent tool conferred
  implicitly now has to be named explicitly, and every gap found since has been
  one more thing that used to be free.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import levers as _levers
from . import transcript as _transcript
from .broker import broker, resolved_requests
from .context import render_card
from .resolve import (
    HARNESS,
    REPO,
    ConfigError,
    Resolved,
    agent_frontmatter,
    cache_paths,
    require_sandbox,
    resolve,
)

#: The plugin's shipped skills, for the `preload` lever.
PLUGIN_ROOT_SKILLS = HARNESS.parent / "skills"

#: Where worktrees this module creates are placed. Same directory Claude Code uses
#: for Agent-tool isolation, so `harness/swarm/worktree-sweep.sh` reclaims ours too
#: rather than needing a second sweeper that would rot independently.
WORKTREE_ROOT = REPO / ".claude" / "worktrees"


class DispatchError(RuntimeError):
    """The dispatch could not be attempted, or its result cannot be trusted."""


def needs_worktree(agent: str) -> bool:
    """Whether this agent must run in its own checkout.

    MEASURED, AND THE REASON THIS FUNCTION EXISTS: `claude -p` does NOT honour
    `isolation: worktree` from agent frontmatter. Dispatching fullstack-engineer
    headless ran it in the primary checkout. Under the Agent tool, Claude Code
    creates the worktree; under this boundary nothing does — so a wave of eight
    workers would all edit the main checkout at once.

    The frontmatter key stays authoritative; this module is what makes it true on
    the boundary path.
    """
    return str(agent_frontmatter(agent).get("isolation") or "") == "worktree"


def prepare_worktree(
    agent: str,
    worker: int,
    lane: str | None = None,
    task: str | None = None,
    resume: str | None = None,
) -> Path:
    """Create and populate an isolated worktree for one worker.

    Reuses harness/swarm/swarm-worktree-init.sh rather than reimplementing it: that
    script already hardlinks the venv, symlinks node_modules and writes the
    per-worker .swarm-env with its own DB_NAME — the invariant a whole test file
    exists to protect.

    `resume` names an EXISTING worker branch that already holds this task's work — what
    `models.resume` / `swarm/resume-point.sh` reports. The worktree is attached to that
    branch (reusing its live worktree if one still exists, else creating one from the
    ref) instead of a new branch being cut from HEAD. Without it, every stoppage after a
    worker started ended with the task re-implemented beside the branch that held it.
    """
    if resume:
        from .resume import _worktrees

        exists = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{resume}"],
            cwd=str(REPO), capture_output=True, text=True,
        )
        if exists.returncode != 0:
            raise DispatchError(f"--resume {resume}: no such branch in {REPO}")
        live = _worktrees(REPO).get(resume)
        if live and live.is_dir():
            path = live  # REATTACH: its uncommitted work exists nowhere else
        else:
            path = WORKTREE_ROOT / resume
            if path.exists():
                raise DispatchError(
                    f"{path} exists but is not the worktree of {resume}. Run "
                    f"`git worktree prune` and `harness/swarm/worktree-sweep.sh`."
                )
            WORKTREE_ROOT.mkdir(parents=True, exist_ok=True)
            add = subprocess.run(
                ["git", "worktree", "add", str(path), resume],
                cwd=str(REPO), capture_output=True, text=True,
            )
            if add.returncode != 0:
                raise DispatchError(f"git worktree add failed: {add.stderr.strip()[:300]}")
    else:
        slug = (task or agent).replace("/", "-")
        name = f"harness-w{worker}-{slug}"
        path = WORKTREE_ROOT / name
        if path.exists():
            raise DispatchError(
                f"{path} already exists. If it holds this task's work, dispatch with "
                f"`--resume {name}`; otherwise reclaim it with "
                f"`harness/swarm/worktree-sweep.sh --apply` before reusing worker {worker}."
            )
        WORKTREE_ROOT.mkdir(parents=True, exist_ok=True)

        add = subprocess.run(
            ["git", "worktree", "add", "-b", name, str(path), "HEAD"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
        )
        if add.returncode != 0:
            raise DispatchError(f"git worktree add failed: {add.stderr.strip()[:300]}")

    # The lane is OPTIONAL and the init script defaults it — `worker.default_lane()`
    # takes the first lane the project declares. Passing None through built an argv
    # containing None and raised `TypeError: expected str, bytes or os.PathLike object`
    # from deep inside subprocess, which names nothing a caller can act on.
    init_argv = [str(HARNESS / "swarm" / "swarm-worktree-init.sh"), str(worker)]
    if lane:
        init_argv.append(lane)
    init = subprocess.run(
        init_argv,
        cwd=str(path),
        capture_output=True,
        text=True,
        timeout=900,
    )
    if init.returncode != 0:
        raise DispatchError(
            f"swarm-worktree-init.sh failed in {path}: {init.stderr.strip()[:300]}"
        )
    return path


def resume_preamble(branch: str, worktree: Path) -> str:
    """What a resumed worker is told before its task. The prompt file is the only
    parent→child channel, so this is where "do not start over" has to live."""
    from .resume import is_dirty, main_branch

    main = main_branch(REPO)
    ahead = subprocess.run(
        ["git", "rev-list", "--count", f"{main}..{branch}"],
        cwd=str(REPO), capture_output=True, text=True,
    ).stdout.strip() or "0"
    dirty = worktree.is_dir() and is_dirty(worktree)
    return (
        f"RESUMING — DO NOT START OVER.\n"
        f"Branch `{branch}` already holds {ahead} commit(s) for this task"
        + (", and this worktree has UNCOMMITTED changes on top of them" if dirty else "")
        + f".\nFirst run `git log --oneline {main}..HEAD` and `git status`, and read what "
        f"is there. Continue from that work; if part of it is wrong, fix it in place. Your "
        f"commit(s) go on this branch. Do not re-create anything that already exists here.\n\n"
    )


@dataclass(frozen=True)
class Outcome:
    """One dispatch's result and its measured cost."""

    resolved: Resolved
    ok: bool
    text: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    turns: int
    duration_ms: int
    session_id: str
    permission_denials: list[Any]
    raw: dict[str, Any]
    #: What the worker's tool results cost it, read back from its transcript. None when
    #: the transcript was not found — an absent measurement, never a zero one.
    results: _transcript.ResultVolume | None = None

    @property
    def budget_exhausted(self) -> bool:
        """Killed by the per-dispatch ceiling. NOT `BLOCKED`: the worker never got to say
        anything, so it never entered the escalation ladder — the orchestrator was left a
        stack trace to interpret. First-class so the swarm can route it."""
        return self.raw.get("subtype") == "error_max_budget_usd"

    @property
    def terminal(self) -> str:
        """Why it ended: `success`, `budget`, `max_turns`, `usage_limit`, `api_error`, `error`.

        `subtype: success` with `is_error: true` is the API-failure shape — the agent loop
        completed but the last turn was an API error — and one of those is the account's
        usage window closing: the CLI returns "You've hit your session limit" as the result,
        one turn, $0. An A/B series ran 26 more runs of exactly that before anyone looked,
        because it read as success. Named so the rig can stop.
        """
        sub = str(self.raw.get("subtype") or "")
        if sub == "error_max_budget_usd":
            return "budget"
        if sub == "error_max_turns":
            return "max_turns"
        if self.raw.get("is_error"):
            text = str(self.raw.get("result") or "").lower()
            if "limit" in text and ("session" in text or "usage" in text or "rate" in text):
                return "usage_limit"
            return str(self.raw.get("terminal_reason") or "api_error")
        if sub == "success":
            return "success"
        return str(self.raw.get("terminal_reason") or "error")

    @property
    def transcript(self) -> list[str]:
        return list(self.raw.get("transcript") or [])

    @property
    def prompt_tokens(self) -> int:
        """Every token sent as prompt across the dispatch: fresh, cache-written, cache-read."""
        return self.input_tokens + self.cache_creation_tokens + self.cache_read_tokens

    @property
    def cache_hit_pct(self) -> float | None:
        """Share of prompt tokens served from cache. THE ONE NUMBER the cost analysis had
        to reconstruct by hand from transcripts: a wave whose workers each start cold
        shows here as a low hit rate, and a resumed agent's near-total miss shows as ~0."""
        return round(100 * self.cache_read_tokens / self.prompt_tokens, 1) if self.prompt_tokens else None

    @property
    def cache_write_pct(self) -> float | None:
        """Share of prompt tokens written to cache — billed at a premium (1.25x at the
        5-minute TTL, 2x at 1-hour), so this is where TTL and prefix choices show up."""
        return round(100 * self.cache_creation_tokens / self.prompt_tokens, 1) if self.prompt_tokens else None

    def telemetry(
        self,
        task: str | None = None,
        attempt: int = 1,
        escalated_from: str | None = None,
    ) -> dict[str, Any]:
        """The payload recorded as an `event` task. Names only — never a token."""
        return {
            "task": task,
            "attempt": attempt,
            "escalated_from": escalated_from,
            **self.resolved.redacted(),
            "ok": self.ok,
            "terminal": self.terminal,
            "experiment": _levers.experiment(),
            "levers": _levers.snapshot(),
            "cost_usd": round(self.cost_usd, 6),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_hit_pct": self.cache_hit_pct,
            "cache_write_pct": self.cache_write_pct,
            "models": sorted((self.raw.get("model_usage") or {}).keys()),
            "turns": self.turns,
            "duration_ms": self.duration_ms,
            **(self.results.telemetry() if self.results else {}),
            "permission_denials": len(self.permission_denials),
            "denied_tools": sorted(
                {
                    d.get("tool_name", "?")
                    for d in self.permission_denials
                    if isinstance(d, dict)
                }
            ),
        }
def with_context(
    prompt: str, lane: str | None, cwd: str | None = None, task: str | None = None
) -> str:
    """The prompt plus the technology card for this lane.

    Injected HERE rather than written into each agent, for the same reason the
    plugin namespace is: an agent that names a technology cannot serve a project
    using a different one. The dispatcher knows the lane, so it can supply exactly
    the rules that apply and none of the ones that do not — which is what keeps the
    cost of supporting many technologies at zero for the ones not in use.
    """
    # The Agent tool cannot be handed an environment, so the same fact is stated in
    # the prompt. Belt and braces: a worker dispatched either way can find the scripts.
    where = (
        "## Where you are\n\n"
        # THE ACTUAL WORKING DIRECTORY, which for a worker is its WORKTREE and not the
        # primary checkout. Naming the primary checkout here was a regression that cost a
        # whole wave: a worker told it was somewhere it was not went looking for
        # harness.yaml, MAD_HARNESS_REPO, `env` and finally ~/.zshrc, collecting ten
        # denials on the way. A prompt that states a falsehood about the environment is
        # worse than one that says nothing.
        f"Your working directory is already `{cwd or REPO}` — the checkout you are "
        "working in, and everything you change belongs there.\n\n"
        # THREE DIRECTORIES, NAMED, because two of them are different repositories and an
        # agent that cannot tell them apart starts investigating. Measured: workers spent
        # denials on `env`, `printenv MAD_HARNESS_REPO`, `GIT_DIR` and `ls` of the parent directory
        # working out which repo they were in. `env` is deliberately NOT granted — the
        # dispatch environment carries provider credentials, and `Resolved.redacted` exists
        # precisely so they never reach an agent's output — so the answer is given here.
        f"- the project under test: `{REPO}`\n"
        f"- your checkout of it: `{cwd or REPO}`\n"
        f"- the harness, a SEPARATE repository whose scripts you invoke: `{HARNESS}`\n\n"
        "Your environment is already correct — the harness set it. Do not inspect it: "
        "`env` and `printenv` are not granted, because the dispatch environment carries "
        "provider credentials that must never reach an agent's output.\n\n"
        "Run "
        "git and the project's own commands plainly, from where you are. Reaching for "
        "`cd <path>; …` or `git -C <path> …` is not only unnecessary, it is DENIED: a "
        "compound command matches no permission rule, and `-C` moves the path in front "
        "of the subcommand so `git log` is no longer what you are running. Measured: "
        "five of seven denials in a live lens run were an agent navigating to the "
        "directory it was already in.\n\n"
        "## Harness scripts\n\n"
        f"They live at `{HARNESS}`, also exported as `$HARNESS_ROOT`.\n\n"
        "**Invoke them by that ABSOLUTE PATH, not through the variable.** Prompts and "
        "skills write `$HARNESS_ROOT/verify/brief.sh` for readability, but a command "
        "spelled with a variable cannot be permitted: permission rules match the command "
        "TEXTUALLY, before the shell expands anything, so no rule matches it and the call "
        "is denied — silently, in a dispatch nobody is watching. Substitute the path "
        "above and the same command is granted.\n\n"
        f"    {HARNESS}/verify/brief.sh <task-id> <sha>\n"
        f"    {HARNESS}/tracker/tk.sh ready --json\n"
    )
    # WHAT AN OPERATOR ALREADY DECIDED. A re-dispatched worker has no memory of its first
    # attempt, so without this it rediscovers the blocked approach and re-asks a question
    # that has been answered.
    answered = resolved_requests(task)

    # The conventions the harness enforces on its own artefacts. Three skills carry
    # this too, which covers every agent that preloads one of them; injecting it here
    # as well means an agent dispatched with no preloaded skill still gets the rules.
    # Same belt-and-braces reasoning recorded above for $HARNESS_ROOT.
    conventions = (
        "## Harness conventions\n\n"
        "- Pair every task id with a short gloss — `PROJ-4f2a (what it is)`, six words max. "
        "The bare id is correct only in commit messages and `bd` arguments.\n"
        "- Cite code by SYMBOL, never by line number, in anything written to a task: "
        "`file.py::function`. A symbol survives edits above it; a line number does not. "
        "Line numbers are fine in your report and in chat.\n"
    )
    card = render_card(lane)
    preload = _preloaded_skills()
    return "\n\n".join(x for x in (prompt, card, where, answered, conventions, preload) if x)


def _preloaded_skills() -> str:
    """Skills named by the `preload` lever, appended in full. In production a skill is
    preloaded through the agent's frontmatter and arrives in the system prompt; this is
    the same text one message later, so an A/B can measure a preload without editing
    the agent — the writers' batching doctrine (evidence-gathering) being the case."""
    from .levers import lever

    parts = []
    for name in lever("preload", block={}):
        path = PLUGIN_ROOT_SKILLS / name / "SKILL.md"
        if not path.is_file():
            raise DispatchError(f"MAD_HARNESS_PRELOAD names {name!r}, but {path} does not exist")
        body = path.read_text()
        body = body.split("---", 2)[2] if body.startswith("---") else body  # drop frontmatter
        parts.append(f"## Preloaded skill: {name}\n\n{body.strip()}")
    return "\n\n".join(parts)


def build_env(r: Resolved, base: dict[str, str] | None = None) -> dict[str, str]:
    """The subprocess environment: inherited, plus this provider's overrides.

    Provider credentials enter here and nowhere else. They are never written to
    tiers.yaml, never rendered into a log line, and never reach a telemetry task
    — see Resolved.redacted().
    """
    env = dict(os.environ if base is None else base)
    env.update(r.env)

    # WHERE THE HARNESS SCRIPTS ARE. Prompts reference `$HARNESS_ROOT/verify/...`
    # rather than a bare `harness/...`, which resolves only when the harness lives
    # inside the repository being worked on — true in-tree, false for every installed
    # plugin. `CLAUDE_PLUGIN_ROOT` is NOT usable for this: it is expanded in plugin
    # config files but is absent from an agent's Bash environment (measured), so a
    # prompt using it would expand to `/harness/...` and fail SILENTLY, which is worse
    # than the honest "no such file" it replaced.
    # THE TOOLCHAIN CACHES, pointed inside the sandbox boundary. Without these a
    # sandboxed `uv` cannot reach ~/.cache/uv and every command it runs fails.
    for var, path in cache_paths().items():
        env[var] = path
        Path(path).mkdir(parents=True, exist_ok=True)
    env["HARNESS_ROOT"] = str(HARNESS)
    # THE PROJECT IS EXPLICIT; THE CHECKOUT IS THE WORKER'S OWN. `MAD_HARNESS_REPO` names
    # the project — config, tracker, primary checkout — for everything the worker
    # invokes. `MAD_HARNESS_CALLER_PWD` must NOT be inherited: it is the dispatcher's
    # directory, and every wrapper the worker calls would keep it ("already set wins"),
    # so `run.sh` ran the worker's tests in the primary checkout and `peek.sh` read the
    # primary's files. Stripped, each wrapper records the worker's own cwd — its
    # worktree — and CHECKOUT resolves there.
    env.setdefault("MAD_HARNESS_REPO", str(REPO))
    env.pop("MAD_HARNESS_CALLER_PWD", None)
    # THE CACHE TTL IS A CHOICE, NOT AN ACCIDENT. Nobody had set it: workers through this
    # path wrote cache at the 1-hour rate (2x) while lenses through the Agent tool wrote at
    # 5-minute (1.25x), a difference nobody chose. A worker turns continuously, so the
    # 5-minute window is rarely missed — but a test suite longer than five minutes IS a
    # miss, and then 1h pays. Measured per project; see models/levers.py.
    from .levers import lever

    ttl = lever("cache_ttl")
    if ttl:
        env["CLAUDE_CODE_PROMPT_CACHE_TTL"] = ttl
    return env


def _run_sdk(
    r: Resolved,
    prompt: str,
    *,
    cwd: str,
    env: dict[str, str],
    timeout: int,
    task: str | None = None,
) -> dict[str, Any]:
    """Run one dispatch through the Agent SDK and return the result message as a dict.

    WHY THE SDK RATHER THAN ARGV WE BUILD OURSELVES. The SDK spawns the same Claude Code
    CLI this used to spawn by hand — but every dispatch bug this harness has had lived in
    the hand-built argv, not in the agent: `--allowedTools` is variadic and ate the prompt;
    the grants had to be one joined string rather than several; `--output-format` had to
    come last to terminate the variadic flag; a `Read(...)` grant looked right and did
    nothing where `--add-dir` was needed. None of those are expressible here.

    The result is a dict rather than the SDK's dataclass so the callers, the telemetry
    record and the tests keep the shape they already read.
    """
    import asyncio

    from claude_agent_sdk import (
        AssistantMessage,
        ResultError,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
        query,
    )

    async def _go() -> dict[str, Any]:
        options = r.sdk_options(cwd=cwd, env=env)
        # THE BROKER RUNS ONLY ON THE DENIAL PATH. Anything the grants already cover is
        # auto-approved and never reaches it, so this widens nothing — it turns a silent
        # refusal into one the agent is told the reason for, and records it beside the
        # wave's telemetry. See models/broker.py.
        options.can_use_tool = broker(r.agent, task=task)
        last: dict[str, Any] = {}
        # THE TRANSCRIPT IS KEPT AS IT STREAMS, because a terminal error arrives as an
        # exception and would otherwise take every turn before it with it. Two workers
        # killed by the budget ceiling left output files holding only a traceback — no
        # turns, no tool calls, nothing to say how far they got or why it cost that much.
        transcript: list[str] = []
        try:
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            transcript.append(block.text.strip())
                        elif isinstance(block, ToolUseBlock):
                            arg = json.dumps(block.input)[:160]
                            transcript.append(f"[tool] {block.name} {arg}")
                elif isinstance(message, ResultMessage):
                    last = {
                        "subtype": message.subtype,
                        "is_error": message.is_error,
                        "result": message.result or "",
                        "total_cost_usd": message.total_cost_usd or 0.0,
                        "usage": message.usage or {},
                        "model_usage": message.model_usage or {},
                        "num_turns": message.num_turns,
                        "duration_ms": message.duration_ms,
                        "session_id": message.session_id,
                        "permission_denials": message.permission_denials or [],
                    }
        except ResultError as exc:
            # A TERMINAL ERROR IS AN OUTCOME, NOT A CRASH. The CLI reported a result —
            # `error_max_budget_usd`, `error_max_turns`, an API error — with the cost,
            # tokens and turns it had accrued, and the SDK hands that payload over on the
            # exception. Returning it lets the caller record the spend (the spend that
            # caused the failure is the one that matters most) and classify the kill
            # instead of interpreting a stack trace.
            data = dict(exc.data or {})
            return {
                "subtype": exc.subtype or data.get("subtype") or "error",
                "is_error": True,
                "result": exc.result or data.get("result") or str(exc),
                "total_cost_usd": data.get("total_cost_usd") or 0.0,
                "usage": data.get("usage") or {},
                "model_usage": data.get("modelUsage") or data.get("model_usage") or {},
                "num_turns": data.get("num_turns") or 0,
                "duration_ms": data.get("duration_ms") or 0,
                "session_id": exc.session_id or data.get("session_id") or "",
                "permission_denials": data.get("permission_denials") or [],
                "terminal_reason": exc.terminal_reason,
                "errors": list(exc.errors or []),
                "transcript": transcript,
            }
        if not last:
            raise DispatchError(
                "the SDK returned no result message — the agent produced nothing at all. "
                "That is a dispatch failure, not an empty answer."
            )
        return last

    try:
        return asyncio.run(asyncio.wait_for(_go(), timeout=timeout))
    except TimeoutError as exc:
        raise DispatchError(f"dispatch exceeded {timeout}s") from exc


def dispatch(
    agent: str,
    prompt: str,
    *,
    override_tier: str | None = None,
    high_risk: bool = False,
    cwd: Path | None = None,
    lane: str | None = None,
    timeout: int = 3600,
    task: str | None = None,
    runner: Any = None,
) -> Outcome:
    """Resolve, invoke, and return the parsed outcome.

    :param runner: injected for tests; defaults to :func:`_run_sdk`.
    """
    # BEFORE ANYTHING ELSE. A dispatch on an unsandboxed machine would run with only the
    # permission rules behind it, which is the posture this project decided not to ship.
    require_sandbox()

    r = resolve(agent, override_tier=override_tier, high_risk=high_risk)
    if r.missing_env:
        raise DispatchError(
            f"provider {r.provider!r} needs {', '.join(r.missing_env)} in the "
            f"environment and it is unset. Refusing to dispatch: an empty "
            f"credential fails as a 401 that reads like a provider outage."
        )

    # An agent that declares worktree isolation must never run in the primary
    # checkout. Refuse rather than proceed: the caller forgot to prepare one, and
    # a wave that proceeds anyway corrupts the main tree in a way no test catches.
    if needs_worktree(agent) and Path(cwd or REPO).resolve() == REPO.resolve():
        raise DispatchError(
            f"{agent} declares `isolation: worktree` but would run in the primary "
            f"checkout. Pass --worker N (dispatch.sh prepares the worktree) or an "
            f"explicit --cwd inside one."
        )

    started = time.monotonic()
    payload = (runner or _run_sdk)(
        r,
        with_context(prompt, lane, cwd=str(cwd or REPO), task=task),
        cwd=str(cwd or REPO),
        env=build_env(r),
        timeout=timeout,
        task=task,
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)

    usage = payload.get("usage") or {}
    return Outcome(
        resolved=r,
        # A DENIAL IS A FAILURE, even though the CLI calls it a success.
        # Measured: headless `-p` does not block on a missing permission — it
        # denies the tool, lets the model carry on, and returns subtype=success
        # with is_error=false. So a worker denied Bash cannot run the tests and
        # still reports PASS. That is the exact shape of the decorative-test
        # failure this repo gates against, arriving through the dispatcher instead
        # of through the worker. Treat any denial as not-ok and let the caller see
        # the list.
        ok=(
            (not payload.get("is_error"))
            and payload.get("subtype") == "success"
            and not (payload.get("permission_denials") or [])
        ),
        text=payload.get("result") or "",
        cost_usd=float(payload.get("total_cost_usd") or 0.0),
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
        cache_creation_tokens=int(usage.get("cache_creation_input_tokens") or 0),
        turns=int(payload.get("num_turns") or 0),
        duration_ms=int(payload.get("duration_ms") or elapsed_ms),
        session_id=payload.get("session_id") or "",
        permission_denials=list(payload.get("permission_denials") or []),
        raw=payload,
        results=_transcript.result_volume_for(cwd or REPO, payload.get("session_id") or ""),
    )
def record(
    outcome: Outcome,
    task: str | None = None,
    attempt: int = 1,
    escalated_from: str | None = None,
) -> bool:
    """Append one dispatch event to the cost series.

    THROUGH THE TELEMETRY PORT, NOT THE TRACKER. This used to write an `event` task,
    which worked only because beads happens to support one; the series itself has no
    dependency-graph or claim semantics and never belonged in a task tracker. The port
    merges the historical event tasks on read, so moving it cost no history and needed
    no import step.

    Telemetry must never fail a dispatch that already succeeded, so a recording failure
    is reported and swallowed — the port guarantees `record` does not raise.
    """
    import tracker

    payload = outcome.telemetry(
        task=task, attempt=attempt, escalated_from=escalated_from
    )
    return tracker.telemetry().record(
        "harness.dispatch", task or outcome.resolved.agent, payload
    )


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(
        prog="dispatch.sh",
        description="Invoke one agent through the CLI boundary and record its cost.",
    )
    ap.add_argument("agent")
    ap.add_argument(
        "--prompt-file",
        type=Path,
        required=True,
        help="the dispatch prompt; a file, because prompts are long "
        "and argv quoting is where a prompt gets silently truncated",
    )
    ap.add_argument("--tier", default=None, help="explicit override (rank 1)")
    ap.add_argument("--high-risk", action="store_true", help="force the policy tier")
    ap.add_argument("--task", default=None, help="task id, for telemetry")
    ap.add_argument("--attempt", type=int, default=1)
    ap.add_argument("--cwd", type=Path, default=None, help="worktree to run in")
    ap.add_argument(
        "--resume",
        default=None,
        help="an existing worker branch already holding this task's work (see "
        "swarm/resume-point.sh); the worker is attached to it and told to continue",
    )
    ap.add_argument(
        "--worker",
        type=int,
        default=None,
        help="worker number; prepares an isolated worktree for agents "
        "that declare `isolation: worktree`",
    )
    ap.add_argument(
        "--lane",
        default=None,
        help="defaults to the first lane declared in harness.yaml",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="print the resolved command and exit without dispatching",
    )
    ap.add_argument("--no-record", action="store_true")
    args = ap.parse_args(argv)

    try:
        if args.dry_run:
            r = resolve(args.agent, override_tier=args.tier, high_risk=args.high_risk)
            print(r)
            o = r.sdk_options(cwd=str(REPO))
            print("  agent:", o.extra_args.get("agent"))
            print("  mode :", o.permission_mode, "| settings:", ",".join(o.setting_sources or []))
            print("  allow:", " ".join(o.allowed_tools) or "(none)")
            if o.disallowed_tools:
                print("  deny :", " ".join(o.disallowed_tools))
            if o.add_dirs:
                print("  dirs :", " ".join(str(d) for d in o.add_dirs))
            if r.missing_env:
                print("  UNSET:", ", ".join(r.missing_env))
                return 1
            return 0

        cwd = args.cwd
        if cwd is None and args.worker is not None and needs_worktree(args.agent):
            cwd = prepare_worktree(args.agent, args.worker, args.lane, args.task, resume=args.resume)
            print(f"-- worktree: {cwd}", file=sys.stderr)

        prompt = args.prompt_file.read_text()
        if args.resume:
            prompt = resume_preamble(args.resume, Path(cwd) if cwd else REPO) + prompt

        outcome = dispatch(
            args.agent,
            prompt,
            override_tier=args.tier,
            high_risk=args.high_risk,
            cwd=cwd,
            lane=args.lane,
            task=args.task,
        )
    except (ConfigError, DispatchError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    if not args.no_record:
        record(outcome, task=args.task, attempt=args.attempt)

    if outcome.terminal != "success" and outcome.transcript:
        # What arrived before the kill, THEN the error — never the error instead of it.
        print(f"-- partial transcript, {len(outcome.transcript)} step(s) before the run ended:")
        for step in outcome.transcript:
            print(f"   {step}")
        print()
    print(outcome.text)
    if outcome.budget_exhausted:
        ceiling = outcome.resolved.max_budget_usd
        print(
            f"\n-- BUDGET EXHAUSTED: ${outcome.cost_usd:.2f} spent"
            + (f" against a ${ceiling:.2f} ceiling" if ceiling else "")
            + f" after {outcome.turns} turn(s). This is not BLOCKED — the worker was cut off. "
            f"Check `resume-point.sh <task>` for uncommitted work, then escalate the tier or "
            f"split the task; do not re-dispatch as-is.",
            file=sys.stderr,
        )
    print(
        f"\n-- {outcome.resolved.provider}/{outcome.resolved.model} "
        f"tier={outcome.resolved.tier} terminal={outcome.terminal} turns={outcome.turns} "
        f"${outcome.cost_usd:.4f} {outcome.duration_ms}ms",
        file=sys.stderr,
    )
    if outcome.prompt_tokens:
        print(
            f"-- cache: {outcome.cache_hit_pct}% read, {outcome.cache_write_pct}% written, "
            f"{outcome.prompt_tokens:,} prompt tokens over {outcome.turns} turn(s); "
            f"{outcome.output_tokens:,} output",
            file=sys.stderr,
        )
    if outcome.permission_denials:
        # WHICH tool was denied is the whole diagnostic value. A bare count tells
        # an operator that something was blocked but not what the agent could not
        # do, and the run is gone by the time they think to ask.
        print(
            f"-- {len(outcome.permission_denials)} permission denial(s) — this is "
            f"work NOT DONE, whatever the return text claims:",
            file=sys.stderr,
        )
        for d in outcome.permission_denials:
            detail = d.get("tool_input") if isinstance(d, dict) else None
            shown = json.dumps(detail)[:160] if detail else ""
            name = d.get("tool_name", "?") if isinstance(d, dict) else str(d)[:40]
            print(f"     {name}: {shown}", file=sys.stderr)
    if outcome.budget_exhausted:
        return EXIT_BUDGET
    return 0 if outcome.ok else 1


#: A budget kill exits distinctly from a worker that ran and failed, so a caller can
#: route it — and so a pipe like `dispatch.sh … | tail` has something to lose.
EXIT_BUDGET = 3


if __name__ == "__main__":
    raise SystemExit(main())
