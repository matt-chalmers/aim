"""Resolve (agent, task) -> the concrete model configuration to dispatch with.

WHY THIS IS ONE MODULE. Precedence is the part of a routing system that rots: it
gets re-implemented slightly differently in the dispatcher, in a check, and in a
test, and then a task runs on a tier nobody chose. There is exactly one
implementation of :func:`resolve`, and everything else calls it.

The precedence, highest first:

    1. explicit task override   an operator or an escalation said so outright
    2. policy                   high-risk work is forced up, whatever step 3 says
    3. agent default            `model_tier:` in the agent's own frontmatter
    4. global default           `default_tier:` in tiers.yaml

Step 2 sits ABOVE the agent default deliberately. An agent's default is a
statement about its ordinary work; a security-sensitive diff is not ordinary
work, and the cheap tier must not be reachable for it by forgetting to pass an
override. Step 1 stays above step 2 so a human can still force a tier down for a
deliberate experiment — but that requires saying so, which is the point.

SECRETS. Provider env may reference ${VAR}. Those are expanded from the
orchestrator's environment at dispatch time and returned in
:attr:`Resolved.env`, which is for handing to a subprocess and nothing else.
Every rendering path here — ``__str__``, :meth:`Resolved.redacted` — reports
names and never values, because this output is printed, logged, and written to
telemetry tasks.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

#: Where the harness code itself lives. As a plugin this is inside the installed
#: plugin directory, which is NOT inside the repository being worked on.
HARNESS = Path(__file__).resolve().parent.parent


def _find_repo() -> Path:
    """The repository the harness is operating ON, which is not where it lives.

    In-tree, the two coincide and `HARNESS.parent` was correct. Installed as a
    plugin they are unrelated directories, so the repo must be found from the
    working directory. `MAD_HARNESS_REPO` overrides for tests and for tools
    that run from outside a checkout.

    THE CONFIG IS A SHARPER SIGNAL THAN THE GIT ROOT, and is tried first. A
    project is defined by its `harness.yaml`, not by where someone happened to run
    `git init`: the two agree for an ordinary consuming repo, whose config sits at
    its root, and diverge exactly where a plugin is nested inside a larger
    repository — a marketplace holding several plugins, or a monorepo. There the
    git root is the *outer* tree, which holds no `harness.yaml`, so every
    config-reading check would look in the wrong place and fail.

    The failure that motivated this is a quiet one: `harness/tests/conftest.py`
    sets `MAD_HARNESS_REPO` explicitly, so the suite stays green under the wrong
    resolution while `make project` breaks in the same breath.
    """
    import os
    import subprocess

    override = os.environ.get("MAD_HARNESS_REPO")
    if override:
        return Path(override).resolve()

    # Walk up from the working directory to the nearest ancestor carrying a
    # project config. `parents` excludes the directory itself, so check it first.
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / "harness.yaml").is_file():
            return candidate

    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip()).resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    # Last resort: the in-tree layout, where the harness sits inside the repo.
    return HARNESS.parent


REPO = _find_repo()


def repo_root() -> Path:
    """The repository the harness is operating on, re-read rather than cached.

    `REPO` is resolved once at import. Anything that runs against a repo chosen after
    this module loads — the tracker CLI under `MAD_HARNESS_REPO`, a test parameterised
    over fixtures — needs the live answer, not the one from import time.
    """
    return _find_repo()
TIERS_FILE = HARNESS / "models" / "tiers.yaml"
ENV_FILE = HARNESS / ".env"
#: The plugin root — one level above the machinery. Agents, commands and skills
#: ship here, so the checks that validate them look here first and fall back to a
#: repo-local `.claude/` for the in-tree layout.
PLUGIN_ROOT = HARNESS.parent


def _prompts_dir(kind: str) -> Path:
    shipped = PLUGIN_ROOT / kind
    return shipped if shipped.is_dir() else REPO / ".claude" / kind


AGENTS_DIR = _prompts_dir("agents")

#: The tier that high-risk work is forced to, regardless of the agent's default.
POLICY_FORCED_TIER = "strategic"

_ENV_REF = re.compile(r"^\$\{([A-Z_][A-Z0-9_]*)\}$")
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


class ConfigError(RuntimeError):
    """tiers.yaml or an agent definition is wrong in a way that must stop a dispatch."""


@dataclass(frozen=True)
class Resolved:
    """One dispatch's fully-resolved configuration."""

    agent: str
    tier: str
    reason: str
    provider: str
    model: str
    effort: str
    max_budget_usd: float
    env: dict[str, str] = field(default_factory=dict)
    missing_env: tuple[str, ...] = ()
    #: `default` for a read-only agent; `acceptEdits` for one that writes. See
    #: :func:`permission_for`.
    permission_mode: str = "default"
    #: Command prefixes an agent may run without an approver.
    allowed_tools: tuple[str, ...] = ()
    #: Directories outside the working tree the agent may read. A grant cannot express
    #: this: see :func:`cli_args`.
    add_dirs: tuple[str, ...] = ()
    #: Commands no agent may run whatever else grants them. DENY BEATS ALLOW (measured),
    #: which is what makes a documented prohibition enforceable.
    disallowed_tools: tuple[str, ...] = ()
    #: Which settings files the dispatched agent loads. See :func:`cli_args`.
    setting_sources: str = "project"
    #: OS-level containment for Bash and its children. See :func:`sandbox_for`.
    sandbox: dict[str, Any] = field(default_factory=dict)
    #: Extra settings handed to the dispatch as JSON, carrying the sandbox's filesystem
    #: policy — which the SDK's `sandbox` option cannot express.
    settings: str = ""
    #: The plugin to load by path, because dropping `user` settings also drops the
    #: installed plugin registration. See :func:`cli_args`.
    plugin_dir: str | None = None

    def sdk_options(self, cwd: str | Path | None = None, env: dict[str, str] | None = None):
        """The Agent SDK options for this dispatch.

        THE ONE PLACE routing and permission decisions become dispatch configuration.
        Everything below was established by measuring a live CLI, and every line of it
        was once wrong in a way that produced a plausible-looking result:

        `permission_mode` AND `allowed_tools` are both required for a writer. With
        neither, `Write` is denied and the worker returns BLOCKED having touched nothing.
        With `acceptEdits` alone the files land but every Bash call is denied, so the
        suite never runs and nothing is committed.

        `add_dirs` is how a lens reads the brief written for it. A `Read(<dir>/**)` grant
        is refused exactly like no grant at all; the directory has to be handed over.

        `setting_sources` is restricted to `project` so the grants here are a CEILING.
        Left to its default the child also loads the developer's own settings, and a lens
        deliberately given no git grant ran `git add -A` with zero denials.

        `plugins` is required BECAUSE of that restriction: dropping `user` settings drops
        the plugin registration, and the agent then fails to resolve by name.

        `disallowed_tools` carries what the corpus forbids, because deny beats allow.
        """
        from claude_agent_sdk import ClaudeAgentOptions

        return ClaudeAgentOptions(
            model=self.model,
            effort=self.effort,
            max_budget_usd=self.max_budget_usd,
            permission_mode=self.permission_mode,
            allowed_tools=list(self.allowed_tools),
            disallowed_tools=list(self.disallowed_tools),
            add_dirs=list(self.add_dirs),
            setting_sources=[self.setting_sources],
            plugins=[{"type": "local", "path": self.plugin_dir}] if self.plugin_dir else [],
            cwd=str(cwd) if cwd else None,
            env=env or {},
            sandbox=self.sandbox or None,
            settings=self.settings or None,
            # THE AGENT IS SELECTED BY NAME, NOT REDEFINED. The SDK's `agents` option takes
            # programmatic definitions; the agents here are files with frontmatter carrying
            # tools, skills and isolation, and rebuilding them out here would fork that
            # into a second definition free to drift. `extra_args` passes the CLI's own
            # `--agent`, so the file stays the single source of truth.
            extra_args={"agent": qualified(self.agent)},
        )

    def redacted(self) -> dict[str, Any]:
        """A form safe to print, log, or write to a telemetry task.

        Env is reduced to the NAMES that were set. A provider token rendered into
        a task is a leak that survives in the exported issues.jsonl, so no code
        path here ever emits a value.
        """
        return {
            "agent": self.agent,
            "tier": self.tier,
            "reason": self.reason,
            "provider": self.provider,
            "model": self.model,
            "effort": self.effort,
            "max_budget_usd": self.max_budget_usd,
            "env_names": sorted(self.env),
            "missing_env": list(self.missing_env),
        }

    def __str__(self) -> str:
        env = ",".join(sorted(self.env)) or "-"
        return (
            f"{self.agent} -> {self.tier} ({self.reason}): "
            f"{self.provider}/{self.model} effort={self.effort} "
            f"budget=${self.max_budget_usd} env=[{env}]"
        )


def load_env_file(path: Path | None = None) -> dict[str, str]:
    """Read harness/.env, if it exists. A real environment variable always wins.

    Deliberately a five-line parser rather than a dependency: the harness installs
    nothing, and the file it reads is a handful of `KEY=value` lines written by
    the repo owner. Anything fancier (interpolation, multiline, export prefixes)
    would be a feature nobody asked for guarding a file that holds three keys.

    Kept separate from the application's root `.env` on purpose — that one is the
    application's own template, and agent-provider credentials do not belong in it.
    """
    path = path or ENV_FILE
    out: dict[str, str] = {}
    try:
        text = path.read_text()
    except FileNotFoundError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if sep:
            out[key.strip()] = value.strip().strip("'\"")
    return out


def effective_environ(environ: dict[str, str] | None = None) -> dict[str, str]:
    """The environment provider references are expanded against."""
    base = load_env_file()
    base.update(os.environ if environ is None else environ)
    return base


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Read and structurally validate tiers.yaml."""
    path = path or TIERS_FILE
    try:
        config = yaml.safe_load(path.read_text()) or {}
    except FileNotFoundError as exc:
        raise ConfigError(f"no tier config at {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc

    tiers = config.get("tiers")
    if not isinstance(tiers, dict) or not tiers:
        raise ConfigError(f"{path} defines no tiers")

    providers = config.get("providers") or {}
    default_tier = config.get("default_tier")
    if default_tier not in tiers:
        raise ConfigError(
            f"default_tier {default_tier!r} is not one of {sorted(tiers)}"
        )

    for name, tier in tiers.items():
        for key in ("provider", "model", "effort", "max_budget_usd"):
            if key not in tier:
                raise ConfigError(f"tier {name!r} is missing {key!r}")
        if tier["provider"] not in providers:
            raise ConfigError(
                f"tier {name!r} names provider {tier['provider']!r}, "
                f"which is not defined; known: {sorted(providers)}"
            )
    if POLICY_FORCED_TIER not in tiers:
        raise ConfigError(
            f"the policy-forced tier {POLICY_FORCED_TIER!r} is not defined; "
            "high-risk work would have nowhere to escalate to"
        )
    return config


def agent_frontmatter(agent: str, agents_dir: Path | None = None) -> dict[str, Any]:
    """The frontmatter of the agent's own frontmatter, parsed leniently.

    LENIENTLY IS LOAD-BEARING, not laziness. Agent frontmatter is not strictly
    valid YAML and does not have to be: ``verifier-security.md`` carries an
    unquoted ``description:`` containing ``TRIGGER-BASED:``, and a bare colon
    inside an unquoted scalar makes ``yaml.safe_load`` raise. Claude Code — the
    actual consumer of these files — parses them happily, so the file is correct
    for its purpose and this module is the odd one out.

    So a strict parse here would mean either crashing on a valid agent, or
    editing agent descriptions to satisfy a *secondary* reader. Both are worse
    than reading the handful of scalar keys we need directly. We try the strict
    parse first (it gives lists like ``tools:`` for free) and fall back to a
    line-scan for top-level scalars when it fails.
    """
    path = (agents_dir or AGENTS_DIR) / f"{agent}.md"
    try:
        text = path.read_text()
    except FileNotFoundError as exc:
        raise ConfigError(f"no agent definition at {path}") from exc
    match = _FRONTMATTER.match(text)
    if not match:
        raise ConfigError(f"{path} has no YAML frontmatter")
    block = match.group(1)
    try:
        parsed = yaml.safe_load(block)
        if isinstance(parsed, dict):
            return parsed
    except yaml.YAMLError:
        pass
    return _scalar_keys(block)


def _scalar_keys(block: str) -> dict[str, Any]:
    """Top-level ``key: value`` scalars from a frontmatter block.

    Indented lines are skipped, so a list or nested mapping is simply absent
    rather than half-read — an absent key is a clean failure, a half-read one is
    not.
    """
    out: dict[str, Any] = {}
    for line in block.splitlines():
        if not line or line[0].isspace() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if sep and key and not key[0].isspace():
            out[key.strip()] = value.strip().strip("'\"") or None
    return out


def provider_env(
    provider: str, config: dict[str, Any], environ: dict[str, str] | None = None
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Expand a provider's ${VAR} env references against the real environment.

    Returns the resolved mapping and the names that were referenced but unset.
    An unset name is reported rather than raised so a *check* can list every
    problem at once; the dispatcher is what refuses to run.
    """
    environ = effective_environ(environ)
    spec = (config.get("providers") or {}).get(provider) or {}
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for key, raw in (spec.get("env") or {}).items():
        ref = _ENV_REF.match(str(raw))
        if not ref:
            resolved[key] = str(raw)  # a literal, e.g. a public base URL
            continue
        value = environ.get(ref.group(1))
        if value:
            resolved[key] = value
        else:
            missing.append(ref.group(1))
    return resolved, tuple(missing)


def plugin_name() -> str | None:
    """This plugin's name, when the harness is running as an installed plugin.

    Claude Code namespaces a plugin's agents — `verifier` shipped by a plugin is
    addressed as `<plugin>:verifier`, and the bare name does NOT resolve to it.
    Measured: dispatching `--agent verifier-spec` with no project-local copy
    returns an empty result, while `--agent mad-harness:verifier-spec` answers.

    So the prefix is applied here rather than written into every prompt: prose
    that names agents stays readable and portable, and a plugin rename is one
    manifest edit instead of a sweep.
    """
    manifest = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
    if not manifest.is_file():
        return None
    try:
        return json.loads(manifest.read_text()).get("name") or None
    except (json.JSONDecodeError, OSError):
        return None


def qualified(agent: str) -> str:
    """`agent`, namespaced if this harness is installed as a plugin."""
    if ":" in agent:
        return agent
    name = plugin_name()
    return f"{name}:{agent}" if name else agent


def permission_for(agent: str, agents_dir: Path | None = None) -> tuple[str, tuple[str, ...]]:
    """The permission mode and command grants this agent needs.

    DERIVED FROM WHAT THE AGENT DECLARES, never from a list of names. An agent whose
    `tools:` includes `Edit` or `Write` is a writer and needs to edit, run its suite and
    commit; everything else is a lens or an analyst, and reads are auto-approved already.
    A name list would go stale the first time an agent changed shape.

    NOT `bypassPermissions`. A writer runs unattended in a worktree, so the grant it gets
    is the grant it has — narrowing it to the toolchains the project actually declares
    costs nothing and is the difference between "may run the suite" and "may run
    anything".
    """
    fm = agent_frontmatter(agent, agents_dir)
    tools = {t.strip() for t in str(fm.get("tools") or "").replace(",", " ").split()}
    if not ({"Edit", "Write"} & tools):
        # A READER IS NOT AN UNGRANTED AGENT. "Reads are auto-approved" holds only inside
        # the working directory, and the two things a lens most needs are outside it: the
        # brief the harness generated for it, which lands under SCRATCHPAD/TMPDIR, and the
        # harness scripts themselves. A live run measured 4-13 denials per lens, including
        # `Read` on the brief and `uv run pytest` — so the lens fell back to judging the
        # worker's claims by reading them, which is the one thing a verification gate
        # exists not to do.
        return "default", _reader_grants()

    grants = [
        # NO `Bash(git:*)`. Under the sandbox git needs no grant: it runs inside the
        # worktree, which IS the sandboxed workspace, so it is auto-approved. MEASURED
        # across two full waves with the grant removed — zero git denials, every task
        # committed and merged. What remains of git policy is the deny on `push`, which
        # the sandbox cannot express because it is a workflow rule, not containment.
        # THE HARNESS SCRIPTS, BY ABSOLUTE PATH AND WITH `/*`. Both details are measured,
        # and the obvious spellings do not work:
        #
        #   Bash(<dir>/*)          ALLOWED   <- this one
        #   Bash(<script>:*)       ALLOWED
        #   Bash(<dir>/:*)         denied    <- what the harness wrote everywhere
        #   Bash($HARNESS_ROOT/…)  denied    <- for ANY rule, see below
        #
        # `<dir>/:*` never matches because the part before `:` is the COMMAND, and a
        # directory is not one. And a command written with a VARIABLE cannot be granted by
        # any rule at all: matching is textual, so `$HARNESS_ROOT/tracker/tk.sh` matches
        # neither the expanded path nor a rule spelled the same way. That is why prompts
        # must invoke these by absolute path — see `dispatch.with_context`.
        *_harness_grants(),
    ]
    grants.extend(_toolchain_grants(grants))
    grants.extend(g for g in _operator_grants() if g not in grants)
    return "acceptEdits", tuple(grants)


def _toolchain_grants(existing: list[str] = []) -> list[str]:
    """One grant per toolchain the PROJECT declares, so a Node repo gets npm and a Python
    one gets uv without either being written down here."""
    out: list[str] = []
    try:
        from .commands import resolve as resolve_command
        from .commands import tool_of
        from .project import load

        for stack in load().stacks:
            for key in ("test", "test_scoped", "lint", "verify"):
                tool = tool_of(resolve_command(stack, key) or "")
                grant = f"Bash({tool}:*)"
                if tool and grant not in existing and grant not in out:
                    out.append(grant)
    except Exception:  # noqa: BLE001 — an unconfigured project still gets git and the harness
        pass
    return out


def _reader_grants() -> tuple[str, ...]:
    """What a lens needs to judge rather than to take the worker's word.

    Deliberately NARROWER than a writer's: no `git` beyond reads it already has, and
    nothing that edits. A lens that cannot run the suite can only believe the report.
    """
    grants = [
        *_harness_grants(),
        # NO READ-ONLY GIT SUBCOMMANDS. They were added because a lens was denied
        # `git ls-tree`, and under the sandbox that denial no longer happens — git reads
        # inside the workspace and are auto-approved. MEASURED over six lens dispatches
        # with them removed: the only git command refused was one inside a COMPOUND, which
        # no prefix rule could ever have matched, so the grants were not what made the
        # difference. A reader carrying eleven more grants than a writer was itself a sign
        # the list had stopped tracking need.
    ]
    grants.extend(_toolchain_grants(grants))
    grants.extend(g for g in _operator_grants() if g not in grants)
    return tuple(grants)


def _operator_grants() -> tuple[str, ...]:
    """Grants a person approved in `harness.yaml`, answering a `permission` request.

    THE ONLY WAY THIS ALLOWLIST GROWS. An agent refused a tool files a request; the
    operator answers, narrowing the rule if they choose; the answer lands in the project
    config and applies to the NEXT dispatch. Nothing an agent runs can write that key —
    `check_commands._NORMATIVE` refuses it — because an agent able to edit it would be
    approving its own request, which is the laundering pattern this design exists to
    avoid.

    A grant here can never defeat `FORBIDDEN`: deny beats allow, measured, so approving
    `Bash(git push:*)` by mistake still leaves pushing refused.
    """
    try:
        from .project import granted_permissions, load

        return granted_permissions(load().raw)
    except Exception:  # noqa: BLE001 — a project without the block simply has no grants
        return ()


#: What no dispatched agent may do, whatever else grants it.
#:
#: `swarm.md` — "a worker must never push — it commits" — and `fullstack-engineer.md`
#: — "not push". Stated in two places, enforced in none, and the writer grant
#: `Bash(git:*)` permitted it outright. A lens has no business pushing either, so this
#: applies to every profile rather than only to writers.
FORBIDDEN: tuple[str, ...] = ("Bash(git push:*)",)


def _harness_grants() -> list[str]:
    """The harness scripts, by absolute path — IN BOTH SPELLINGS THE LOADER PRODUCES.

    Skills invoke these as `${CLAUDE_PLUGIN_ROOT}/harness/...`, and whether that expands
    to `<root>/harness/...` or `<root>//harness/...` depends on whether the installed
    plugin root carries a trailing slash. Matching is TEXTUAL, so the doubled form matches
    no rule and the call is denied — measured in a live run, where every `tk.sh memories`
    and `check-line-pins.sh` call a lens made was refused for exactly this reason. One
    extra rule costs nothing and removes an environment-dependent failure.
    """
    return [f"Bash({HARNESS}/*)", f"Bash({HARNESS.parent}//{HARNESS.name}/*)"]


def cache_paths() -> dict[str, str]:
    """Toolchain cache locations, as `ENV_VAR -> absolute path`.

    Declared by the stack modules, because only a toolchain knows it keeps a cache at all.
    Resolved against the PRIMARY CHECKOUT rather than the worktree, so eight workers share
    one cache instead of each re-populating its own.
    """
    out: dict[str, str] = {}
    try:
        from .project import load

        root = repo_root()
        for stack in load().stacks:
            for var, rel in stack.cache_env.items():
                out[var] = str(root / rel)
    except Exception:  # noqa: BLE001 — an unconfigured project still gets a sandbox
        pass
    return out


def sandbox_for() -> tuple[dict[str, Any], str]:
    """The OS containment for a dispatch, and the settings that complete it.

    WHY THE SANDBOX IS THE BOUNDARY AND THE GRANTS ARE NOT. Permission rules match command
    TEXT, so a compound command matches nothing however safe its parts, and a worker that
    needed one simply stopped. The sandbox decides what an action can REACH instead, for
    the command and every child process, which is a property no text rule can have.
    Measured, with `allowed_tools` empty in both runs: a write inside a compound command
    was refused six times unsandboxed, and ran sandboxed with zero denials — while the
    write itself, which targeted a path outside the workspace, was still refused by the
    OS. That is the whole design in one result.

    THE CACHE IS MOVED INSIDE THE BOUNDARY rather than a hole being cut to reach it.
    Granting `~/.cache/uv` was measured and does not work — uv fails EPERM on a file
    inside the granted directory — and relocating the cache under the primary checkout
    both works and pokes no hole into the home directory. See `cache_paths`.

    `allowUnsandboxedCommands` IS FALSE DELIBERATELY. The first sandboxed agent to meet a
    restriction reached straight for `dangerouslyDisableSandbox`. Leaving that escape
    hatch open would make the boundary advisory.

    THE FILESYSTEM POLICY COMES FROM THE STACKS, because only a toolchain knows what it
    writes outside the repository. Confining a worker to its worktree is correct right up
    until `uv` cannot open `~/.cache/uv` and every test command fails with "Operation not
    permitted" — measured. It is passed as `settings` because the SDK's `sandbox` option
    has no filesystem field.
    """
    writable = list(cache_paths().values())

    sandbox = {
        "enabled": True,
        "autoAllowBashIfSandboxed": True,
        "allowUnsandboxedCommands": False,
    }
    settings = json.dumps({"sandbox": {"filesystem": {"allowWrite": writable}}}) if writable else ""
    return sandbox, settings


class SandboxUnavailable(RuntimeError):
    """The sandbox this harness requires is not available on this machine."""


def sandbox_available() -> tuple[bool, str]:
    """Whether OS containment can actually be enforced here, and what is missing.

    A HARD REQUIREMENT NEEDS A HARD CHECK. The harness supports sandboxed machines only,
    and the failure mode of not checking is the worst one available: on a machine without
    the sandbox every dispatch would quietly fall back to permission rules alone — the
    posture this project has decided not to support — and nothing would say so. Absence
    read as fine is the defect this corpus gates against everywhere else.
    """
    import platform
    import shutil

    system = platform.system()
    if system == "Darwin":
        # Seatbelt ships with macOS; nothing to install.
        return (True, "") if shutil.which("sandbox-exec") else (False, "sandbox-exec is missing")
    if system == "Linux":
        if shutil.which("bwrap"):
            return True, ""
        return False, (
            "bubblewrap is not installed. Install it (`apt install bubblewrap`, "
            "`dnf install bubblewrap`) and the optional seccomp filter."
        )
    return False, (
        f"{system} has no supported sandbox. Run the harness inside WSL2, where "
        f"bubblewrap is available."
    )


def require_sandbox() -> None:
    """Refuse to dispatch where the boundary cannot be enforced."""
    ok, why = sandbox_available()
    if not ok:
        raise SandboxUnavailable(
            f"this harness dispatches only on machines where the sandbox can be "
            f"enforced, and it cannot be here: {why}. Refusing rather than falling back "
            f"to permission rules alone, which is a posture this project does not support."
        )


def briefs_root() -> str:
    """Where `verify/brief.py` writes, which is outside the repository by design.

    Briefs are scratch, so they follow SCRATCHPAD/TMPDIR — and a lens dispatched into a
    repo cannot read them without being handed the directory.
    """
    return str(
        (
            Path(os.environ.get("SCRATCHPAD") or os.environ.get("TMPDIR") or "/tmp")
            / "harness-briefs"
        ).resolve()
    )


def resolve(
    agent: str,
    *,
    override_tier: str | None = None,
    high_risk: bool = False,
    config: dict[str, Any] | None = None,
    agents_dir: Path | None = None,
    environ: dict[str, str] | None = None,
) -> Resolved:
    """Apply the precedence chain and return the configuration to dispatch with.

    :param override_tier: an explicit operator or escalation decision (rank 1)
    :param high_risk: the task touches a security-sensitive surface (rank 2)
    """
    config = config or load_config()
    tiers = config["tiers"]

    if override_tier is not None:
        if override_tier not in tiers:
            raise ConfigError(f"unknown tier {override_tier!r}; known: {sorted(tiers)}")
        tier, reason = override_tier, "explicit override"
    elif high_risk:
        tier, reason = POLICY_FORCED_TIER, "policy: high-risk surface"
    else:
        declared = agent_frontmatter(agent, agents_dir).get("model_tier")
        if declared is None:
            tier, reason = config["default_tier"], "global default"
        elif declared not in tiers:
            raise ConfigError(
                f"agent {agent!r} declares model_tier {declared!r}, "
                f"which is not defined; known: {sorted(tiers)}"
            )
        else:
            tier, reason = declared, "agent default"

    spec = tiers[tier]
    env, missing = provider_env(spec["provider"], config, environ)
    mode, grants = permission_for(agent, agents_dir)
    _sandbox, _settings = sandbox_for()
    return Resolved(
        agent=agent,
        tier=tier,
        reason=reason,
        provider=spec["provider"],
        model=spec["model"],
        effort=spec["effort"],
        max_budget_usd=float(spec["max_budget_usd"]),
        env=env,
        missing_env=missing,
        permission_mode=mode,
        allowed_tools=grants,
        # A writer works in its worktree; a lens has to read the brief the harness wrote
        # for it, which is never in the repo.
        # THE HARNESS ITSELF IS READABLE. Agents are told to invoke these scripts, so
        # they reasonably try to read one to learn its interface — and a worker's worktree
        # does not contain them. Measured: two of six denials in a live wave were exactly
        # that, both caused by guidance pointing at a script the reader could not open.
        # Read-only, and it is the harness's own source.
        # THE THREE DIRECTORIES THE PROMPT NAMES ARE ALL READABLE. Naming a path invites
        # an agent to look at it — measured twice, first when a lens was given the repo
        # path and reached for `git -C`, then when workers were told the project root and
        # spent four denials listing it. A worker's worktree already holds the same
        # tracked content, so this grants nothing it could not already see; it just stops
        # the looking from failing.
        add_dirs=(
            (str(HARNESS), str(REPO))
            if mode == "acceptEdits"
            else (briefs_root(), str(HARNESS), str(REPO))
        ),
        disallowed_tools=FORBIDDEN,
        plugin_dir=str(PLUGIN_ROOT),
        sandbox=_sandbox,
        settings=_settings,
    )
