"""Resolve (agent, task) -> the concrete model configuration to dispatch with.

WHY THIS IS ONE MODULE. Precedence is the part of a routing system that rots: it
gets re-implemented slightly differently in the dispatcher, in a check, and in a
test, and then a task runs on a tier nobody chose. There is exactly one
implementation of :func:`resolve`, and everything else calls it.

The precedence, highest first:

    1. explicit task override   an operator or an escalation said so outright
    2. policy                   high-risk work is forced up, whatever steps 3-5 say
    3. project tier override    `agent_tiers:` in the consuming project's harness.yaml
    4. agent default            `model_tier:` in the agent's own frontmatter
    5. global default           `default_tier:` in tiers.yaml

Step 2 sits ABOVE the agent default deliberately. An agent's default is a
statement about its ordinary work; a security-sensitive diff is not ordinary
work, and the cheap tier must not be reachable for it by forgetting to pass an
override. Step 1 stays above step 2 so a human can still force a tier down for a
deliberate experiment — but that requires saying so, which is the point.

Step 3 is the field's switch. A consuming project moves an agent between tiers
without patching the plugin — the A/B the cost analysis asked for on the
verifiers (`Project.tiers` has the numbers), which stays a switch and not a
default because a verification gate's catch rate has to be measured before it
moves for everyone. It sits BELOW policy so a project cannot lower a high-risk
dispatch by configuration; lowering one still takes step 1, said outright, per
dispatch. The reason recorded is :data:`PROJECT_OVERRIDE`, so a telemetry series
can be split by arm without guessing from the tier.

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


class RepoError(RuntimeError):
    """The harness could not tell which repository it is operating on."""


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

    # WALK UP FROM THE CALLER'S DIRECTORY, NOT OURS. Every shell wrapper does
    # `cd "$(dirname "$0")/.."` before exec'ing Python, so by the time this runs the
    # working directory is the HARNESS — which carries its own `harness.yaml`,
    # describing the harness as a project. The walk below then finds that config and
    # stops, and the check answers about the wrong repository without erroring.
    #
    # Measured: `check-stack-commands.sh` run by hand from a consuming project reported
    # the plugin's own `python-uv-selftest` stack. It looks like a pass.
    #
    # So the wrappers record where they were invoked from before they move. When the
    # harness is being developed the caller's directory IS the harness, which resolves
    # to the harness — still correct.
    caller = os.environ.get("MAD_HARNESS_CALLER_PWD")
    cwd = Path(caller).resolve() if caller else Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / "harness.yaml").is_file():
            return candidate

    # The git fallback runs FROM THE CALLER'S DIRECTORY too. Run from the process's
    # cwd it answered about the harness's own checkout — the marketplace repository
    # in-tree, the plugin cache when installed — which is never the project.
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(cwd),
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip()).resolve()
    except (OSError, subprocess.SubprocessError):
        pass

    # THE HARNESS'S OWN TREE IS NEVER THE ANSWER FOR A CALLER OUTSIDE IT. Installed as a
    # plugin, `HARNESS.parent` is the plugin cache: it carries a `harness.yaml`, so every
    # tracker call would run against a directory with no records and return an empty
    # result with exit 0 — an unattended campaign read that as "the backlog is
    # exhausted" and reported a clean, zero-work run against 17 open epics. Refusing is
    # the only safe answer: nothing above found a project, so say so, with the path tried.
    raise RepoError(
        f"no project found from {cwd}: no harness.yaml in it or any parent, and it is "
        f"not inside a git checkout. Run from the consuming repository, or set "
        f"MAD_HARNESS_REPO to its root."
    )


REPO = _find_repo()


def _find_checkout() -> Path:
    """The git working tree the CALLER stands in — which is not always the project.

    REPO is the project: its config, its tracker, its primary checkout. A dispatched
    worker stands in its own WORKTREE, and that is where the code under test lives. The
    two were one variable, so `run.sh` ran a worker's tests in the primary checkout — a
    green that said nothing about the worker's change, and never its own DB — and
    `peek.sh` showed a worker the primary's copy of a file it had just edited. A worker
    caught it: it broke its implementation on purpose, run.sh stayed green, and it filed
    the bug. Falls back to REPO outside any git tree.
    """
    import os
    import subprocess

    caller = os.environ.get("MAD_HARNESS_CALLER_PWD")
    cwd = Path(caller).resolve() if caller else Path.cwd().resolve()
    # THE PROJECT ROOT WITHIN THIS CHECKOUT — the nearest harness.yaml above the caller,
    # the same walk REPO uses without the override. Not the git toplevel: a project that
    # sits inside a larger repository (this plugin inside its marketplace) has a checkout
    # root that is not its project root, and every path would miss.
    for candidate in (cwd, *cwd.parents):
        if (candidate / "harness.yaml").is_file():
            return candidate
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=15, cwd=str(cwd),
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip()).resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    return REPO


CHECKOUT = _find_checkout()


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
#: WHO ENFORCES `max_budget_usd` ON A PRICED TIER: the harness, and nothing else.
#:
#: The CLI checks that flag against its own price table. For a model it does not know that
#: table is a fiction — measured twice against DeepSeek off-peak, it reported 10.3x and
#: 9.7x the real cost — so its kill lands at a real-dollar figure NOBODY CAN STATE. This
#: passed it a loosened multiple for a while (10x, then 25x) as a "backstop", which was the
#: same mistake in a smaller font: a threshold in an unknown currency is not a bound, and
#: at 10x it was close enough to race the meter it was meant to back up.
#:
#: So a priced tier hands the CLI no ceiling at all, and the one enforcer is the one that
#: knows the rates. What the backstop was really for — a provider that streams no usage —
#: is now caught twice in terms the harness can state: `probe-compat.sh` certifies streamed
#: token accounting before a provider is routed at all, and `dispatch.py` stops a dispatch
#: that has run `UNMETERED_TURNS_ALLOWED` turns without a single usage payload rather than
#: continue under a ceiling nothing is checking.
UNMETERED_TURNS_ALLOWED = 3
#: Model words that resolve differently per dispatch path; a tier must name a concrete id.
MODEL_ALIASES = frozenset({"opus", "sonnet", "haiku", "fable", "inherit", "default"})
#: The reason recorded when a project's `agent_tiers:` block moved the agent. Telemetry
#: carries it verbatim, which is how an A/B series is split by arm.
PROJECT_OVERRIDE = "project override (harness.yaml agent_tiers)"

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
    #: `plugin` when the tier is as tiers.yaml ships it, `project` when the consuming
    #: project's harness.yaml redefined it. In every dispatch record, so a cost series
    #: never silently mixes a project's `worker` with the plugin's.
    tier_source: str = "plugin"
    #: The tier's published rates, where it is not on Anthropic — see models/pricing.py.
    #: Empty means the SDK's own cost figure is the record, which is right for Anthropic.
    #: Per-Mtok rates for THIS tier's model, read from its provider's `models` map. Empty
    #: on Anthropic, where the SDK's own figure is the vendor's accounting; required off it.
    price: dict[str, Any] = field(default_factory=dict)
    #: WHICH POCKET THIS DISPATCH SPENDS FROM: `metered` (billed per token, the default and
    #: the conservative reading) or `subscription` (drawn from a plan's allowance). Both are
    #: real money — a subscription-dollar consumed is one that is no longer available, and
    #: when the allowance runs out the work stops — but they are not the same dollar and a
    #: report that summed them would say a run cost what neither pocket paid.
    billing: str = "metered"
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
    #: An API-side token budget the model is told about so it can pace and wrap up, from
    #: The skills this agent declares, in full — its doctrine, part of its system prompt.
    doctrine: str = ""
    #: the tier's `task_budget_tokens`. None = not set. Distinct from `max_budget_usd`,
    #: which is a circuit breaker the model never sees.
    task_budget_tokens: int | None = None
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

        from .levers import lever

        # A STATIC PREFIX IS WHAT LETS A WAVE SHARE ONE CACHE. The CLI's preset embeds the
        # working directory and git status in the system prompt, and every worker's
        # worktree is a different directory — so eight workers on identical doctrine are
        # eight cold prefixes. With the dynamic sections moved into the first user message
        # the system prompt is byte-identical across the wave. Measured before default.
        system_prompt: dict[str, Any] = {"type": "preset", "preset": "claude_code"}
        if lever("static_prefix"):
            system_prompt["exclude_dynamic_sections"] = True
        # THE DOCTRINE IS PART OF THE SYSTEM PROMPT. Not a message — a message is what a
        # compaction summarises away and what a lever once switched off — and not left to
        # the CLI, which does not preload frontmatter skills on the `--agent` path
        # (measured 0.10.8). The append is cached from the first request and identical
        # for every dispatch of the same agent.
        if self.doctrine:
            system_prompt["append"] = self.doctrine
        # THE SKILL CATALOG IS PAID ON EVERY REQUEST. The Skill tool lists every skill the
        # session can see — the plugin's, the CLI's 17 bundled ones (dataviz, claude-api,
        # keybindings-help...) and the plugin's slash commands, which a dispatched worker
        # can neither use nor must ever run. Naming the plugin's skills here makes the
        # catalog exactly those: measured 26,130 -> 22,743 tokens on a worker's first
        # request. The SDK carries the list as `Skill(name)` grants, which is also what
        # keeps /swarm and /halt out of a worker's reach.
        skills = catalog_skills() if lever("lean_catalog") else None
        return ClaudeAgentOptions(
            model=self.model,
            effort=self.effort,
            # THE HARNESS METERS A PRICED TIER ITSELF (models/pricing.py::Meter, applied in
            # dispatch.py's stream), so the CLI is given NO ceiling for one: it would be
            # checking this number against its own table for a model it does not know, and
            # a kill at an unstatable real-dollar figure is not a bound. See
            # UNMETERED_TURNS_ALLOWED for what replaced the backstop.
            max_budget_usd=None if self.price else self.max_budget_usd,
            system_prompt=system_prompt,
            skills=skills,
            task_budget={"total": self.task_budget_tokens} if self.task_budget_tokens else None,
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
            "tier_source": self.tier_source,
            "task_budget_tokens": self.task_budget_tokens,
            "doctrine_chars": len(self.doctrine),
            "env_names": sorted(self.env),
            "missing_env": list(self.missing_env),
        }

    def __str__(self) -> str:
        env = ",".join(sorted(self.env)) or "-"
        redefined = " (redefined by project)" if self.tier_source == "project" else ""
        return (
            f"{self.agent} -> {self.tier}{redefined} ({self.reason}): "
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


def _read_config(path: Path) -> dict[str, Any]:
    """tiers.yaml as written, unvalidated."""
    try:
        return yaml.safe_load(path.read_text()) or {}
    except FileNotFoundError as exc:
        raise ConfigError(f"no tier config at {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc


def _validate(config: dict[str, Any], where: str = "tiers.yaml") -> dict[str, Any]:
    """Every structural check the tier config must pass — run on the MERGED config, since
    validating before the project's patch would judge a config nobody runs."""
    tiers = config.get("tiers")
    if not isinstance(tiers, dict) or not tiers:
        raise ConfigError(f"{where} defines no tiers")

    providers = config.get("providers") or {}
    for name, block in providers.items():
        billing = (block or {}).get("billing")
        if billing is not None and billing not in ("metered", "subscription"):
            raise ConfigError(f"provider {name!r}: billing must be 'metered' or 'subscription', got {billing!r}")
    default_tier = config.get("default_tier")
    if default_tier not in tiers:
        raise ConfigError(
            f"default_tier {default_tier!r} is not one of {sorted(tiers)}"
        )

    for name, tier in tiers.items():
        if not isinstance(tier, dict):
            raise ConfigError(f"tier {name!r} must be a map of provider/model/effort/max_budget_usd")
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
    # A PRICE IS A FACT ABOUT A MODEL AT A PROVIDER, NOT ABOUT A TIER. It lived on the tier
    # in 0.10.32-0.10.36, which made it a second source of truth the moment two tiers shared
    # a model — and the plugin's own `strong` and `strategic` ARE one model, so routing the
    # escalation ladder at a third-party provider meant writing one rate twice, with nothing
    # comparing them. Two tiers could declare different rates for the same model and both
    # validate; one of them is then wrong, and every cost record and ceiling from that tier
    # is wrong with no symptom.
    #
    # This is not the rule below that a provider must not name a model. That rule is about
    # CHOOSING the model, which the tier owns. A `models:` map is keyed BY model id and
    # chooses nothing; it states what the provider charges for models it serves.
    from .pricing import validate as _validate_price

    for name, spec in providers.items():
        models = (spec or {}).get("models") or {}
        if not isinstance(models, dict):
            raise ConfigError(f"provider {name!r}: `models` must be a map of model id to its facts")
        for mid, mspec in models.items():
            if not isinstance(mspec, dict):
                raise ConfigError(f"provider {name!r} model {mid!r} must be a map (currently only `price`)")
            unknown = set(mspec) - {"price"}
            if unknown:
                raise ConfigError(f"provider {name!r} model {mid!r}: unknown key(s) {', '.join(sorted(unknown))}")
            if "price" in mspec:
                try:
                    _validate_price(mspec["price"], f"provider {name!r} model {mid!r}")
                except ValueError as exc:
                    raise ConfigError(str(exc)) from exc

    for name, tier in tiers.items():
        if "price" in tier:
            raise ConfigError(
                f"tier {name!r} declares `price`, which moved to the provider in 0.11.0 "
                f"because a rate belongs to a model, not to a role — two tiers on one model "
                f"had to state it twice and could disagree. Write it as:\n"
                f"    providers:\n      {tier['provider']}:\n        models:\n"
                f"          {tier['model']}:\n            price: {{...}}"
            )
        if tier["provider"] == "anthropic":
            continue
        priced = ((providers.get(tier["provider"]) or {}).get("models") or {}).get(tier["model"]) or {}
        if not priced.get("price"):
            raise ConfigError(
                f"tier {name!r} resolves to {tier['provider']}/{tier['model']}, which declares no "
                f"`price`. The CLI would price its tokens from its own table (measured: $5.00/Mtok "
                f"for DeepSeek against $0.66-1.32 published, ~10x end to end), and that number "
                f"reaches every cost record and the ceiling. Declare the published rates under "
                f"`providers.{tier['provider']}.models.{tier['model']}.price` — see models/pricing.py."
            )

    # THE TIER OWNS THE MODEL, and names it concretely. A provider env naming a model
    # (`ANTHROPIC_MODEL`) would be a second source of truth; a `${...}` model is one; and a
    # bare alias (`opus`) resolved to different generations on different dispatch paths
    # (measured — it invalidated a parity experiment). These were tests on tiers.yaml;
    # with a project patching the config they are rules on the merged one.
    for name, spec in providers.items():
        for key in (spec or {}).get("env") or {}:
            if "MODEL" in str(key).upper():
                raise ConfigError(f"provider {name!r} names a model in its env ({key}); the tier owns that")
    for name, tier in tiers.items():
        model = str(tier.get("model", ""))
        if not model or "${" in model:
            raise ConfigError(f"tier {name!r} must name a concrete model, got {tier.get('model')!r}")
        if model.lower() in MODEL_ALIASES:
            raise ConfigError(f"tier {name!r} names the alias {model!r}; use a concrete model id so every dispatch path resolves it the same way")
    # THE LADDER, ON THE MERGED CONFIG. escalate.next_tier raises on an off-ladder tier —
    # at dispatch time, on the one path where a silent wrong direction is expensive. A
    # project that adds a tier must place it; a ladder naming a ghost, or the same rung
    # twice, or leaving the policy-forced tier off, fails here by name.
    ladder = config.get("ladder")
    if ladder is not None:
        if not isinstance(ladder, list) or not ladder:
            raise ConfigError(f"{where}: `ladder` must be a non-empty list of tier names, weakest first")
        ghosts = [t for t in ladder if t not in tiers]
        if ghosts:
            raise ConfigError(f"ladder names tier(s) that do not exist: {', '.join(map(str, ghosts))}")
        dupes = sorted({t for t in ladder if ladder.count(t) > 1})
        if dupes:
            raise ConfigError(f"ladder names tier(s) more than once: {', '.join(dupes)}")
        missing = [t for t in tiers if t not in ladder]
        if missing:
            raise ConfigError(f"tier(s) defined but not on the ladder: {', '.join(missing)} — a tier that exists must be placed, or escalation cannot reason about it")
        if POLICY_FORCED_TIER not in ladder:
            raise ConfigError(f"the policy-forced tier {POLICY_FORCED_TIER!r} is not on the ladder")
    return config


def _project_model_config() -> dict[str, Any]:
    """The consuming project's `model_config()` block, or `{}` where there is no project
    or its config cannot be read — the plugin's own defaults then stand, exactly as
    `orchestrator_domains` treats an unconfigured project. A project whose block is
    present but malformed is NOT swallowed: that is a config error the check must show."""
    try:
        from .project import ProjectError, load
    except ImportError:  # pragma: no cover — import cycle guard
        return {}
    try:
        project = load()
    except ProjectError:
        return {}
    except Exception:  # noqa: BLE001 — no project (the plugin's own checkout, a scratch dir)
        return {}
    return project.model_config()


def merge_model_config(plugin: dict[str, Any], project: dict[str, Any]) -> dict[str, Any]:
    """The project's patch over the plugin's defaults.

    MAPS PATCH, SCALARS AND LISTS REPLACE. `tiers` merge by tier name and, within a tier,
    per key — a project supplying only `max_budget_usd` inherits provider, model and
    effort, so a plugin upgrade that changes a budget still reaches consumers. `providers`
    merge by name and, within one, `env` per key. `default_tier` (a scalar) and `ladder`
    (an ordered list) are replaced wholesale when the project names them: a ladder merged
    per index would route escalation somewhere nobody chose. `{**plugin, **project}` keeps
    a redefined tier in its original position and appends new ones, which is what
    `Project.agent_tiers`' "declaration order, weakest first" relies on.
    """
    out: dict[str, Any] = dict(plugin)
    # WHAT THE PROJECT TOUCHED, kept beside the result so every reader can say so: the
    # config check prints it, `Resolved.tier_source` carries it into every dispatch
    # record, and the A/B rig refuses to read a series that mixes the two as one sample.
    # This replaces the guarantee being given up — a project may now redefine even the
    # policy-forced tier; the protection is visibility, not prevention.
    prov: dict[str, Any] = {"tiers": [], "providers": [], "default_tier": False, "ladder": False,
                            "shipped": {name: dict(spec) for name, spec in (plugin.get("tiers") or {}).items()}}
    if "tiers" in project:
        merged = dict(plugin.get("tiers") or {})
        for name, patch in (project["tiers"] or {}).items():
            merged[name] = {**(merged.get(name) or {}), **(patch or {})}
            prov["tiers"].append(name)
        out["tiers"] = merged
    if "providers" in project:
        merged = dict(plugin.get("providers") or {})
        for name, patch in (project["providers"] or {}).items():
            base = dict(merged.get(name) or {})
            patch = dict(patch or {})
            if "env" in patch or "env" in base:
                patch["env"] = {**(base.get("env") or {}), **(patch.get("env") or {})}
            # `models` patches PER MODEL ID, and within one, per key — a project correcting
            # one rate must not drop the others declared beside it, for the same reason a
            # stack's `commands` merge rather than replace.
            if "models" in patch or "models" in base:
                models = {k: dict(v or {}) for k, v in (base.get("models") or {}).items()}
                for mid, mpatch in (patch.get("models") or {}).items():
                    models[mid] = {**(models.get(mid) or {}), **(mpatch or {})}
                patch["models"] = models
            merged[name] = {**base, **patch}
            prov["providers"].append(name)
        out["providers"] = merged
    for key in ("default_tier", "ladder"):
        if key in project:
            out[key] = project[key]
            prov[key] = True
    out["provenance"] = prov
    return out


def provenance(config: dict[str, Any]) -> dict[str, Any]:
    """What a project redefined in this config, or nothing for the plugin's own."""
    return config.get("provenance") or {"tiers": [], "providers": [], "default_tier": False, "ladder": False, "shipped": {}}


def load_config(path: Path | None = None, *, merge_project: bool = True) -> dict[str, Any]:
    """tiers.yaml, patched by the consuming project's `tiers:`/`providers:`/
    `default_tier:`/`ladder:` blocks, then validated — the config that RUNS.

    `merge_project=False` is the plugin's shipped defaults alone: what `check_config.py`
    compares agent frontmatter against, because a project's deliberate redefinition is not
    drift. The same distinction `resolve(project_tiers={})` already draws for selection.
    """
    path = path or TIERS_FILE
    config = _read_config(path)
    if merge_project:
        project = _project_model_config()
        if project:
            config = merge_model_config(config, project)
            return _validate(config, where=f"{path} patched by harness.yaml")
    return _validate(config, where=str(path))


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


def _skill_dirs(root: Path) -> list[str]:
    return sorted(d.name for d in root.iterdir() if (d / "SKILL.md").is_file()) if root.is_dir() else []


def plugin_skills() -> list[str]:
    """Every skill the plugin ships, plugin-qualified."""
    return [qualified(n) for n in _skill_dirs(_prompts_dir("skills"))]


def project_skills(repo: Path | None = None) -> list[str]:
    """Every skill the consuming project ships under `.claude/skills/`, by bare name."""
    return _skill_dirs((repo or REPO) / ".claude" / "skills")


def catalog_skills(repo: Path | None = None) -> list[str]:
    """The Skill catalog a dispatched agent sees under `lean_catalog`: the plugin's skills
    and the project's own. The CLI treats the list as an allowlist that matches a bare
    name exactly or a qualified one by suffix, so a project skill listed by its bare
    name stays visible and invocable. Measured (0.10.8): a list of the plugin's skills
    alone silently hid a project's `.claude/skills/*` — listed nowhere, and an invoke
    rejected as `not in this session's skills allowlist`, which is not a permission
    denial and so never reached the dispatcher. The union is what makes this safe."""
    return plugin_skills() + project_skills(repo)


def _lever(name: str, block: dict[str, Any] | None = None) -> Any:
    from .levers import lever

    return lever(name, block)


def doctrine(agent: str, agents_dir: Path | None = None, extra: tuple[str, ...] = ()) -> str:
    """Every skill the agent declares (and any the `preload` arm adds), in full, as one
    block for the system prompt. A declared skill that does not exist is a config error
    and the dispatch does not start: doctrine that cannot be delivered is not optional."""
    names = list(declared_skills(agent, agents_dir))
    names += [n for n in extra if n not in names]
    parts = []
    for name in names:
        path = _prompts_dir("skills") / name / "SKILL.md"
        if not path.is_file():
            raise ConfigError(f"{agent} declares skill {name!r}, but {path} does not exist")
        body = path.read_text()
        body = body.split("---", 2)[2] if body.startswith("---") else body
        parts.append(f"# Skill: {name}\n\n{body.strip()}")
    return "\n\n".join(parts)


def declared_skills(agent: str, agents_dir: Path | None = None) -> list[str]:
    """The agent's frontmatter `skills:` list, read by line so an agent whose frontmatter
    is not strict YAML (see `agent_frontmatter`) still yields it."""
    path = (agents_dir or AGENTS_DIR) / f"{agent}.md"
    match = _FRONTMATTER.match(path.read_text())
    if not match:
        return []
    out, inside = [], False
    for line in match.group(1).splitlines():
        if not line.startswith((" ", "\t")):
            inside = line.split(":", 1)[0].strip() == "skills"
            continue
        if inside:
            item = line.strip()
            if item.startswith("- "):
                out.append(item[2:].strip().strip("'\""))
    return out


def qualified(agent: str) -> str:
    """`agent`, namespaced if this harness is installed as a plugin."""
    if ":" in agent:
        return agent
    name = plugin_name()
    return f"{name}:{agent}" if name else agent


def _task_budget(spec: dict[str, Any]) -> int | None:
    """Env (the rig's per-arm value), else the project's `dispatch.task_budget_tokens`,
    else the tier's `task_budget_tokens`. Measured: told its budget, a worker paced —
    -32% per run with the spreads separated — so the worker tier carries a default; a
    project whose tasks are larger than the lab's raises it in harness.yaml."""
    from .levers import lever

    override = lever("task_budget")
    if override:
        return int(override)
    return int(spec["task_budget_tokens"]) if spec.get("task_budget_tokens") else None


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
        # brief the harness generated for it (now under the project's run dir), and the
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
    if is_orchestrator(agent, agents_dir):
        # The loop's own verbs, outside any worktree: git in the primary checkout (merge,
        # commit the export, pull, push) and the project's make targets. A worker gets
        # neither: its git is auto-approved inside its worktree and it never pushes.
        grants.extend(g for g in ("Bash(git:*)", "Bash(make:*)") if g not in grants)
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

#: The domains a dispatched ORCHESTRATOR may reach. The sandbox confines every child
#: process's egress to `sandbox.network.allowedDomains`, and the harness sets none for a
#: worker — its toolchain caches sit inside the boundary, and a worker never pushes. An
#: orchestrator pushes, so the boundary opens for exactly the remote and nothing else.
ORCHESTRATOR_DOMAINS: tuple[str, ...] = ("github.com",)


#: THE DISPATCHER RUNS OUTSIDE AN ORCHESTRATOR'S SANDBOX. Nested inside it, the worker it
#: spawns cannot log in: the CLI keeps the OAuth credential in the macOS keychain, which
#: Seatbelt does not reach — measured, "Not logged in · Please run /login" on every nested
#: worker, and keychain errors on the orchestrator's own git. Excluding exactly the
#: dispatch wrapper gives a nested worker today's topology — an unsandboxed dispatcher
#: (which also runs the worktree init) and its own sandbox around the worker. Both
#: spellings the loader produces, as for the grants.
#:
#: AND EVERY SCRIPT THAT DISPATCHES ON THE ORCHESTRATOR'S BEHALF. 0.10.21–0.10.26 gave
#: the orchestrator `lens-gate.sh`, `fanout.sh` and `plan-epic.sh`, each of which runs
#: `dispatch.sh` itself — from INSIDE the orchestrator's sandbox, since only the wrapper
#: was excluded. Measured (the first orchestrated wavelab run of 0.10.27): the survey
#: dispatch under `plan-epic.sh` died in 73ms, "Not logged in · Please run /login", and
#: the campaign stopped at §3a having closed nothing. The exclusion is by the command the
#: orchestrator TYPES, so each such script is listed; the test pins the list to the
#: modules that name `dispatch.sh` as an executable.
DISPATCHING_SCRIPTS: tuple[str, ...] = (
    "models/dispatch.sh",
    "swarm/lens-gate.sh",
    "swarm/fanout.sh",   # runs a jobs file whose lines are dispatch.sh commands
    "swarm/plan-epic.sh",
    "swarm/campaign.sh",  # the outer loop; never typed by an orchestrator, listed for the same reason if it ever is
)
DISPATCHER_OUTSIDE: tuple[str, ...] = tuple(
    spelling
    for rel in DISPATCHING_SCRIPTS
    for spelling in (f"{HARNESS}/{rel}:*", f"{HARNESS.parent}//{HARNESS.name}/{rel}:*")
)


def orchestrator_domains() -> tuple[str, ...]:
    """The remote, plus each declared stack's package index (`network:` in the module) —
    because nested under an orchestrator, a worktree's init runs inside its sandbox.
    Measured: a nested worker died at `uv sync`, files.pythonhosted.org denied."""
    out = list(ORCHESTRATOR_DOMAINS)
    try:
        from .project import load

        for stack in load().stacks:
            out.extend(h for h in stack.network if h not in out)
    except Exception:  # noqa: BLE001 — an unconfigured project still gets the remote
        pass
    return tuple(out)


def is_orchestrator(agent: str, agents_dir: Path | None = None) -> bool:
    """`role: orchestrator` in the agent's frontmatter — the one agent that runs the loop
    rather than a task in it: it merges into the primary checkout and pushes."""
    return str(agent_frontmatter(agent, agents_dir).get("role") or "").strip() == "orchestrator"


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


def sandbox_for(network: tuple[str, ...] = (), excluded: tuple[str, ...] = ()) -> tuple[dict[str, Any], str]:
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

    # EVERYTHING SANDBOX GOES IN THE SANDBOX OPTION. The SDK transport builds the child's
    # settings as `settings_obj["sandbox"] = options.sandbox` — it REPLACES the settings
    # file's sandbox block, so a `filesystem` or `network` key written into `settings`
    # never reached the CLI. Measured (0.10.14): an orchestrator's `allowedDomains` in
    # `settings` left github.com denied; the same key here opens it.
    sandbox: dict[str, Any] = {
        "enabled": True,
        "autoAllowBashIfSandboxed": True,
        "allowUnsandboxedCommands": False,
    }
    if writable:
        sandbox["filesystem"] = {"allowWrite": writable}
    if network:
        sandbox["network"] = {"allowedDomains": list(network)}
    if excluded:
        sandbox["excludedCommands"] = list(excluded)
    # NO CLOUD CONNECTORS FOR A DISPATCH. The CLI attaches the account's claude.ai MCP
    # connectors to every session: measured, a "Claude Docs" connector connected on every
    # worker dispatch (~1.2s) and put ~500 tokens of its instructions into every worker's
    # first message — for tools the agent's `tools:` ceiling never lets it call.
    return sandbox, json.dumps({"disableClaudeAiConnectors": True})


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
    """Where `verify/brief.py` writes: inside the project, under its gitignored run
    directory. It followed SCRATCHPAD/TMPDIR once, and a lens had to be handed the
    directory — which broke the moment the writer and the dispatcher ran in different
    environments (a sandbox sets its own TMPDIR); every lens in a headless epic was denied
    `Read` on its own brief. A lens's working directory needs no grant."""
    return str((REPO / ".harness" / "run" / "briefs").resolve())


def diff_root() -> str:
    """Where `verify/brief.py` writes the DIFF artefacts: beside the briefs, never under
    them, so the one lens that must not see the diff can be denied exactly this root.
    `verify.brief.DIFF_ROOT` is the same path; a test pins that they agree."""
    return str((REPO / ".harness" / "run" / "briefs-diff").resolve())


def sees_no_diff(agent: str, agents_dir: Path | None = None) -> bool:
    """`evidence: no-diff` in the agent's frontmatter — the lens whose independence rests
    on never seeing the change, only the task and the repository as it now stands.

    UNTIL 0.10.21 THIS WAS A SENTENCE. `verification-gate` said "L3 must never see the
    diff", `brief.md` printed the diff's paths in a section every lens read, and every
    reader was granted the directory that held them; `docs/concepts/verification.md`
    called the separation "physical, not instructional" and it was the reverse. The
    frontmatter key turns it into a `Read(//<diff root>/**)` DENY on the dispatch —
    deny beats allow (measured for `git push`), and Claude Code applies Read denies to
    `cat`, `head`, `tail` and `sed` as well as to the Read tool."""
    return str(agent_frontmatter(agent, agents_dir).get("evidence") or "").strip() == "no-diff"


def project_tier_overrides(
    config: dict[str, Any] | None = None, agents_dir: Path | None = None
) -> dict[str, str]:
    """The consuming project's `agent_tiers:` overrides, validated — `{}` where there is no
    harness.yaml to read.

    A missing config is the old behaviour, not an error: the block is optional and a
    dry-run from outside any project resolved before it existed. A config that IS there
    but malformed raises, because a misspelt agent or tier that fell through to the
    agent default would leave an A/B arm silently running on the tier it meant to move
    off — the series would read as "no effect" and the switch would never be trusted.
    """
    # project.py imports this module, so the cycle stays lazy — the same way
    # `dispatch.py` reaches `declared_skills`.
    from .project import PROJECT_FILE, load

    if not PROJECT_FILE.is_file():
        return {}
    return load().agent_tiers(config=config, agents_dir=agents_dir)


def resolve(
    agent: str,
    *,
    override_tier: str | None = None,
    high_risk: bool = False,
    project_tiers: dict[str, str] | None = None,
    config: dict[str, Any] | None = None,
    agents_dir: Path | None = None,
    environ: dict[str, str] | None = None,
) -> Resolved:
    """Apply the precedence chain and return the configuration to dispatch with.

    :param override_tier: an explicit operator or escalation decision (rank 1)
    :param high_risk: the task touches a security-sensitive surface (rank 2)
    :param project_tiers: the project's per-agent overrides (rank 3); None reads them
        from harness.yaml, `{}` asks for the plugin's own defaults regardless of it
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
        if project_tiers is None:
            project_tiers = project_tier_overrides(config=config, agents_dir=agents_dir)
        declared = agent_frontmatter(agent, agents_dir).get("model_tier")
        if agent in project_tiers:
            # `Project.tiers` has validated the block by the time it gets here; the
            # frontmatter is still read above so an agent that does not exist fails the
            # same way it always has, and the tier is checked once more because this
            # argument can also be handed in directly.
            if project_tiers[agent] not in tiers:
                raise ConfigError(
                    f"project override routes {agent!r} to unknown tier "
                    f"{project_tiers[agent]!r}; known: {sorted(tiers)}"
                )
            tier, reason = project_tiers[agent], PROJECT_OVERRIDE
        elif declared is None:
            tier, reason = config["default_tier"], "global default"
        elif declared not in tiers:
            raise ConfigError(
                f"agent {agent!r} declares model_tier {declared!r}, "
                f"which is not defined; known: {sorted(tiers)}"
            )
        else:
            tier, reason = declared, "agent default"

    spec = tiers[tier]
    providers = config.get("providers") or {}
    env, missing = provider_env(spec["provider"], config, environ)
    mode, grants = permission_for(agent, agents_dir)
    orchestrator = is_orchestrator(agent, agents_dir)
    _sandbox, _settings = sandbox_for(
        network=orchestrator_domains() if orchestrator else (),
        excluded=DISPATCHER_OUTSIDE if orchestrator else (),
    )
    return Resolved(
        agent=agent,
        tier=tier,
        reason=reason,
        tier_source="project" if tier in provenance(config)["tiers"] else "plugin",
        # FROM THE PROVIDER'S MODEL, not the tier: one rate per model, stated once.
        price=dict((((providers.get(spec["provider"]) or {}).get("models") or {})
                    .get(spec["model"]) or {}).get("price") or {}),
        billing=str((providers.get(spec["provider"]) or {}).get("billing") or "metered"),
        provider=spec["provider"],
        model=spec["model"],
        effort=spec["effort"],
        # The role's own ceiling, when it has one: an orchestrator's is per epic.
        max_budget_usd=float(
            (config.get("orchestrator") or {}).get("max_budget_usd", spec["max_budget_usd"])
            if orchestrator
            else spec["max_budget_usd"]
        ),
        task_budget_tokens=_task_budget(spec),
        doctrine=doctrine(agent, agents_dir, extra=tuple(_lever("preload", block={}))),
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
        disallowed_tools=() if orchestrator else FORBIDDEN + ((f"Read(//{diff_root().lstrip('/')}/**)",) if sees_no_diff(agent, agents_dir) else ()),
        plugin_dir=str(PLUGIN_ROOT),
        sandbox=_sandbox,
        settings=_settings,
    )
