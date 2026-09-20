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

WHY THE RESULT IS A FILE, AND STDOUT A DIGEST. A dispatch's result lands in the caller's
context, and the caller is the orchestrator — the fattest context in the system.
Measured over one field campaign: the orchestrator ran 237 requests with its context
growing 55k -> 920k tokens, ~380k on average, so every tool call it makes re-reads
~$0.11-0.17 of context — ~6x what the same call costs a worker. That context was 35%
its own outputs, 35% injected text and 7% tool results; the four largest injected texts
were subagent results of 45k, 43k, 36k and 23k chars, and each arrived TWICE — once as
the tool result and again as the task notification — and was then re-sent on every
later turn. So every dispatch writes its whole result under `RESULT_DIR` and prints the
path; `--digest` prints only the first lines. The orchestrator passes the artefact on by
PATH (`tk.sh note <id> --file`, `peek.sh <path>:START-END`) and pays for it nowhere.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import levers as _levers
from . import transcript as _transcript
from .broker import broker, resolved_requests
from .context import render_card, render_invariants
from .project import ProjectError
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

#: Where worktrees this module creates are placed. Same directory Claude Code uses
#: for Agent-tool isolation, so `harness/swarm/worktree-sweep.sh` reclaims ours too
#: rather than needing a second sweeper that would rot independently.
WORKTREE_ROOT = REPO / ".claude" / "worktrees"

#: Where every dispatch's full result goes: the project's run directory, beside the
#: telemetry, ignored by git with the rest of `.harness/run/`. The same directory
#: `commands.LOG_DIR` names under a worker's checkout, so one `peek.sh` habit covers
#: both. The project, not the caller's checkout: the orchestrator dispatches from the
#: primary and the path it prints must survive the worktree the result describes.
RESULT_DIR = REPO / ".harness" / "run" / "out"
#: What `--digest` prints when given no number. A verdict, a task list or a design's
#: summary fits; a 45k-char result does not, and that is the point.
DIGEST_LINES = 40


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


def claim_first(task: str, worker: int) -> str | None:
    """Claim `task` under the worker's actor before the dispatch is paid for. Returns the
    reason to skip (held by another actor), or None when the claim is held — freshly or
    re-entrantly, which is what a resumed worker sees."""
    try:
        import tracker

        actor = f"swarm-w{worker}"
        result = tracker.coordination().try_claim(task, actor)
    except Exception as exc:  # noqa: BLE001 — a tracker that cannot answer must not stop a dispatch silently
        print(f"-- claim: could not ask the tracker ({exc.__class__.__name__}); the worker claims for itself", file=sys.stderr)
        return None
    if result.held:
        print(f"-- claim: {task} held by {actor}{' (re-entrant)' if result.reentrant else ''}", file=sys.stderr)
        return None
    return f"{task} is already claimed by {result.holder} — not dispatched; the dispatch's fixed cost was not paid"


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
    prompt: str,
    lane: str | None,
    cwd: str | None = None,
    task: str | None = None,
    agent: str | None = None,
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
    # The security lens's checklist, from the config it would otherwise have to find.
    invariants = render_invariants(agent)
    # The agent's doctrine is in its SYSTEM PROMPT (resolve.doctrine), not here: a message
    # is what a compaction summarises and what a lever once switched off.
    return "\n\n".join(x for x in (prompt, card, invariants, where, answered, conventions) if x)


#: WHAT A CHILD MUST NOT INHERIT FROM THE DISPATCHER. The SDK builds the child's
#: environment as `{**os.environ, **options.env}` — the dispatcher's own process
#: environment, with the options merged OVER it — so a variable merely absent from
#: `build_env`'s result is inherited anyway. Measured (0.10.14): every worker got the
#: dispatcher's `MAD_HARNESS_CALLER_PWD` — `dispatch.sh`'s wrapper exports it before it
#: exec's — so `run.sh` in a worktree still resolved CHECKOUT to the primary checkout,
#: kept its log there, and 19 of 21 lab workers went reading the wrappers to find out why.
#: 0.10.3 fixed this in the tests and not in a dispatch. And `VIRTUAL_ENV` from the
#: operator's shell made every `uv run` warn, which is what started the reading.
#: The only fix the merge allows is to remove them from THIS process before the SDK
#: spawns; `build_env` pops them too, for the copy it returns.
#: `TRACKER_READONLY` is in the list for the opposite reason: it is SET per child by
#: `build_env` for a reader, and a writer must never inherit it from a dispatcher that
#: happens to be one — the merge would make the writer's `tk.sh close` refuse, silently.
STRIPPED_FROM_CHILDREN: tuple[str, ...] = ("MAD_HARNESS_CALLER_PWD", "VIRTUAL_ENV", "TRACKER_READONLY")


def scrub_process_env() -> None:
    """Remove from the dispatcher's own environment what no child may inherit. Safe here:
    REPO and CHECKOUT were resolved from `MAD_HARNESS_CALLER_PWD` at import, and nothing
    in this process reads it again."""
    for key in STRIPPED_FROM_CHILDREN:
        os.environ.pop(key, None)


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
    # See STRIPPED_FROM_CHILDREN: popping here covers the returned copy; the process
    # itself is scrubbed in `_run_sdk`, which is the pop that reaches a child.
    for key in STRIPPED_FROM_CHILDREN:
        env.pop(key, None)
    # A READER'S TRACKER IS READ-ONLY BY ENVIRONMENT, not by a flag it remembers. Seven
    # agent files each carried "`tk.sh --readonly` for every tracker call"; a lens that
    # forgot the flag once could `tk.sh close`. The reader profile (`permission_for`:
    # no Edit/Write in `tools:`) is `permission_mode == "default"`; `tk.sh` refuses every
    # write verb under this variable exactly as under `--readonly`. Set AFTER the strip,
    # which is what keeps a writer from inheriting it (see STRIPPED_FROM_CHILDREN).
    if r.permission_mode == "default":
        env["TRACKER_READONLY"] = "1"
    # THE CACHE TTL IS A CHOICE, NOT AN ACCIDENT. Nobody had set it: workers through this
    # path wrote cache at the 1-hour rate (2x) while lenses through the Agent tool wrote at
    # 5-minute (1.25x), a difference nobody chose. A worker turns continuously, so the
    # 5-minute window is rarely missed — but a test suite longer than five minutes IS a
    # miss, and then 1h pays. Measured per project; see models/levers.py.
    from .levers import lever

    # The bundled-skills half of the lean catalog: the CLI's own switch for the 17
    # skills it ships. Measured 26,130 -> 23,241 on its own; the `skills` list in
    # `sdk_options` takes the rest.
    if lever("lean_catalog"):
        env["CLAUDE_CODE_DISABLE_BUNDLED_SKILLS"] = "1"
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

    scrub_process_env()

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
                    progress["turns"] += 1
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

    # A KILLED DISPATCH STILL LEAVES A RECORD. The cost is the SDK's, in the result
    # message that never arrives at a timeout; but the turns were counted as they
    # streamed. Measured: an orchestrator cut off at its hour left no event at all, and
    # the series read as if it had never run — its 87 requests had to be recovered from
    # the transcript on disk. The exception carries what was seen; `dispatch` records it.
    progress: dict[str, int] = {"turns": 0}
    try:
        return asyncio.run(asyncio.wait_for(_go(), timeout=timeout))
    except TimeoutError as exc:
        err = DispatchError(f"dispatch exceeded {timeout}s")
        err.turns = progress["turns"]  # type: ignore[attr-defined]
        raise err from exc


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
    try:
        payload = (runner or _run_sdk)(
            r,
            with_context(prompt, lane, cwd=str(cwd or REPO), task=task, agent=agent),
            cwd=str(cwd or REPO),
            env=build_env(r),
            timeout=timeout,
            task=task,
        )
    except DispatchError as exc:
        if str(exc).startswith("dispatch exceeded"):
            record_timeout(r, task, turns=getattr(exc, "turns", 0), elapsed_ms=int((time.monotonic() - started) * 1000))
        raise
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


def render_result(outcome: Outcome) -> str:
    """What the dispatch has to say, in the shape stdout carried before it was a file:
    what arrived before a kill, THEN the result — never the error instead of it."""
    parts: list[str] = []
    if outcome.terminal != "success" and outcome.transcript:
        parts.append(f"-- partial transcript, {len(outcome.transcript)} step(s) before the run ended:")
        parts += [f"   {step}" for step in outcome.transcript]
        parts.append("")
    parts.append(outcome.text)
    return "\n".join(parts)


def keep_result(text: str, agent: str, task: str | None, out: Path | None = None) -> Path | None:
    """Write the whole result where the caller can pass it on by path.

    Default `RESULT_DIR/dispatch-<agent>-<task or adhoc>-<HHMMSS>.md`; a name already
    taken gets a numeric suffix rather than being overwritten, because two dispatches of
    one agent in the same second is exactly a wave. `out` overrides; a relative `out`
    resolves against the project, the same rule `tracker.render._in_project` holds,
    because every wrapper `cd`s into the harness before Python starts.

    Never fails the dispatch. An unwritable directory means no path — and the caller
    then prints in full, because a digest with nowhere to point is a result lost.
    """
    try:
        if out is None:
            slug = (task or "adhoc").replace("/", "-")
            stem = f"dispatch-{agent.replace('/', '-')}-{slug}-{time.strftime('%H%M%S')}"
            RESULT_DIR.mkdir(parents=True, exist_ok=True)
            path = RESULT_DIR / f"{stem}.md"
            n = 1
            while path.exists():
                n += 1
                path = RESULT_DIR / f"{stem}-{n}.md"
        else:
            path = out if out.is_absolute() else REPO / out
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text if text.endswith("\n") else text + "\n")
        return path
    except OSError:
        return None


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


def record_timeout(r: Resolved, task: str | None, *, turns: int, elapsed_ms: int) -> None:
    """The event for a dispatch the timeout killed: `terminal: timeout`, the turns seen,
    the cost unknown (None, never 0 — a zero would read as free). Swallows recording
    failures as `record_event` does."""
    try:
        import tracker

        payload = {
            "task": task, "attempt": 1, "escalated_from": None, **r.redacted(),
            "ok": False, "terminal": "timeout", "experiment": _levers.experiment(), "levers": _levers.snapshot(),
            "cost_usd": None, "turns": turns, "duration_ms": elapsed_ms, "permission_denials": 0, "denied_tools": [],
        }
        tracker.telemetry().record("harness.dispatch", task or r.agent, payload)
    except Exception as exc:  # noqa: BLE001 — telemetry never fails a dispatch
        print(f"warning: timeout event not recorded: {exc}", file=sys.stderr)


def build_parser():
    """The CLI, as one function so a test can parse every documented invocation against
    the real flags rather than a copy of them — a vocabulary sweep once renamed a public
    flag as a side effect of a prose pass, and only luck kept the prompts consistent."""
    import argparse

    ap = argparse.ArgumentParser(
        prog="dispatch.sh",
        description="Invoke one agent through the CLI boundary and record its cost.",
    )
    ap.add_argument("agent")
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument(
        "--prompt-file",
        type=Path,
        help="the dispatch prompt; a file, because prompts are long "
        "and argv quoting is where a prompt gets silently truncated",
    )
    which.add_argument(
        "--task-prompt",
        action="store_true",
        help="assemble the prompt from the task itself (`worker_prompt.build`: the record, how "
        "to run and commit here, the SPEC INDEX slice, the memory keys, a fidelity defect list); "
        "needs --task; --prompt-extra adds what the tracker does not hold",
    )
    ap.add_argument("--prompt-extra", type=Path, default=None, help="with --task-prompt: a file appended under 'From the orchestrator'")
    ap.add_argument("--no-claim", action="store_true", help="do not claim the task before spawning a writer (the default claims it under the worker's actor)")
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
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"where the full result is written; default {RESULT_DIR}/dispatch-<agent>-<task>-<HHMMSS>.md",
    )
    ap.add_argument(
        "--digest",
        nargs="?",
        const=DIGEST_LINES,
        type=int,
        default=None,
        metavar="N",
        help=f"print only the first N lines of the result (default {DIGEST_LINES}; the number "
        "must directly follow the flag) and then `... full: <path> (<M> lines)`; without it "
        "the whole result is printed, then `full: <path>`",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

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

        if args.task_prompt and not args.task:
            print("FAIL: --task-prompt needs --task", file=sys.stderr)
            return 2

        # CLAIM BEFORE SPAWNING. A worker's first act is `tk.sh claim`; one that finds the
        # task held by a sibling returns SKIPPED — after the dispatch has paid its whole
        # fixed base (~18.7k tokens) to learn it. The dispatcher asks first, under the
        # actor the worker will use (`swarm-w<n>`, from `worker.env_block`), so the
        # worker's own claim is re-entrant and a lost claim costs nothing.
        if args.task and args.worker is not None and needs_worktree(args.agent) and not args.no_claim:
            skipped = claim_first(args.task, args.worker)
            if skipped:
                print(f"SKIPPED: {skipped}", file=sys.stderr)
                return 2

        cwd = args.cwd
        if cwd is None and args.worker is not None and needs_worktree(args.agent):
            cwd = prepare_worktree(args.agent, args.worker, args.lane, args.task, resume=args.resume)
            print(f"-- worktree: {cwd}", file=sys.stderr)

        if args.task_prompt:
            from .worker_prompt import build as build_prompt

            extra = args.prompt_extra.read_text() if args.prompt_extra else ""
            prompt = build_prompt(args.task, lane=args.lane, worker=args.worker, extra=extra)
            kept_prompt = keep_result(prompt, f"prompt-{args.agent}", args.task)
            if kept_prompt is not None:
                print(f"-- prompt: {kept_prompt}", file=sys.stderr)
        else:
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
    except (ConfigError, DispatchError, ProjectError) as exc:
        # ProjectError: the project's `tiers:` block names an agent or tier that does
        # not exist. Routing on the agent's default instead would run the A/B on the
        # wrong arm and record it as the right one — so it stops here, like any other
        # config that cannot be dispatched.
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    if not args.no_record:
        record(outcome, task=args.task, attempt=args.attempt)

    body = render_result(outcome)
    kept = keep_result(body, args.agent, args.task, out=args.out)
    if kept is None:
        print(
            f"-- result NOT kept: could not write under {args.out or RESULT_DIR}; printed in full",
            file=sys.stderr,
        )
    if args.digest is not None and kept is not None:
        lines = body.splitlines()
        shown = lines[: max(0, args.digest)]
        if shown:
            print("\n".join(shown))
        print(f"{'... ' if len(lines) > len(shown) else ''}full: {kept} ({len(lines)} lines)")
    else:
        print(body)
        if kept is not None:
            print(f"full: {kept}")
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
