# `harness.yaml`

The one file that makes the harness fit your repository. Written by
[`/harness-setup`](../getting-started.md), verified by `check-project-config.sh`.

## Blocks

| block | declares | required |
|---|---|---|
| `name` / `slug` | display name; lowercase slug that names per-worker resources | yes |
| `harness` | `version`: the plugin version this config was reviewed against — the pre-flights stop when the plugin is newer; see [Upgrading](../upgrading.md) | no, but unstamped is treated as behind |
| `stacks` | which toolchains, and where each lives | yes |
| `frameworks` | how to write good code here — independent of the toolchain | no |
| `tracker` | which backend, and where its records live | no (defaults to `beads`) |
| `beads` | the id prefix the task-hygiene checks build their patterns from | yes, on the beads backend |
| `swarm` | wave sizing and worker resources | no |
| `ports` | every TCP port your servers bind, by name — the pre-flight probes them | no |
| `dispatch` | cost levers, each a measured switch: `cache_ttl`, `static_prefix`, `stagger_seconds`, `task_budget_tokens`, `lean_catalog`, `plan_tiers` — see [`models/levers.py`](../../harness/models/levers.py) | no (`task_budget_tokens` defaults from the tier, `lean_catalog` and `plan_tiers` on; the rest off) |
| `agent_tiers` | per-agent tier overrides, agent → tier — **selection**, see [Model config](#model-config) | no |
| `tiers` | the tier DEFINITIONS, patched over the plugin's — **definition**, see [Model config](#model-config) | no |
| `providers` | the provider set: endpoint, credential and `billing` — see [Model config](#model-config) | no |
| `default_tier` | where an agent declaring no `model_tier:` lands — replaces the plugin's outright | no |
| `ladder` | escalation order, weakest first — replaces the plugin's outright, never interleaved; every defined tier must be on it | no |
| `paths` | docs, staging, archive — **omit any your project lacks** | yes |
| `domain` | your domain vocabulary — prompts are guarded against naming it | no |
| `lanes` | concurrency per lane, measured on your hardware | no |
| `areas` | groups changed paths, and decides which lens a change must face | no |
| `security` | the security surface — worth a real conversation, not a guess | no |
| `testing` | where tests live and what the project holds itself to | yes |
| `layout` | roles → paths (the API surface, the services boundary…) | no |
| `lenses` | extra lenses to dispatch — the extension point | no |
| `signals` | health baselines, which are **measurements** of this repo | no |
| `permissions` | grants an operator approved — see below | no |

Full annotated example: [`templates/harness.yaml.example`](../../templates/harness.yaml.example).

## Model config

Five blocks decide which model runs an agent and what it costs. They answer two questions
that are deliberately kept apart — **which tier** an agent runs on, and **what that tier
is** — because conflating them is how a project ends up unable to change a model without
editing an agent.

| block | answers | shape |
|---|---|---|
| `agent_tiers` | *which tier* — per agent | `agent: tier` |
| `default_tier` | *which tier* — when an agent declares none | a tier name |
| `tiers` | *what a tier is* | patched per key over the plugin's |
| `providers` | how a provider is reached, and which pocket pays | patched per name |
| `ladder` | where escalation goes next | an ordered list, weakest first |

**Patch, do not replace — for `tiers` and `providers`.** Keys you leave out stay the
plugin's, so an upgrade that retunes a ceiling or moves a model still reaches you. `model`
and `provider` move together: naming one without the other is refused rather than silently
half-applied.

**Replace outright — for `default_tier` and `ladder`.** A partially-overridden escalation
order is not a meaningful object, so these substitute the plugin's entirely; every tier you
define must appear on the ladder.

```yaml
# WHICH TIER an agent runs on.
agent_tiers:
  verifier-spec: worker      # tier-split one lens to measure its catch rate lower down

default_tier: strong

# WHAT A TIER IS. Only the keys you state; the rest stay the plugin's.
tiers:
  worker:
    max_budget_usd: 6.00     # this project's tasks carry more to read than the lab's
  strategic:
    provider: deepseek       # provider and model move together
    model: deepseek-v4-pro
    price:                   # REQUIRED off Anthropic — see concepts/providers.md
      input_per_mtok: 1.32
      output_per_mtok: 3.96

# HOW A PROVIDER IS REACHED, and which pocket pays.
providers:
  anthropic:
    billing: subscription    # a plan's allowance; `metered` is the default
  deepseek:
    env:
      ANTHROPIC_BASE_URL: ${DEEPSEEK_BASE_URL}
      ANTHROPIC_AUTH_TOKEN: ${DEEPSEEK_API_KEY}

ladder: [worker, strong, strategic]
```

Three rules this block is checked against, each by `check-project-config.sh`:

1. **A credential is a `${VAR}` reference, never a value.** `harness.yaml` is committed. The
   reference is resolved from the environment (or `harness/.env`) at dispatch time and never
   written to a record, a task or telemetry — `env_names` carries names only.
2. **A tier off Anthropic declares a `price`.** Without one the CLI's own table would price
   a model it does not recognise, and every cost series would carry a plausible fiction.
   Why, and what the block contains: [providers](../concepts/providers.md).
3. **`model:` is a concrete id, never an alias.** `opus` resolved to two different model
   generations on two dispatch paths in the same session, which invalidated a whole parity
   experiment before anyone noticed.

Precedence between selection sources, and what each dispatch records about the decision:
[dispatch](dispatch.md#tier-resolution).

## `permissions` — normative, and agent-unwritable

```yaml
permissions:
  allow:
    - Bash(curl https://pypi.org/*)   # answered PROJ-4f2a, narrowed from Bash(curl:*)
```

The only way the allowlist grows. An agent may **request** a grant by filing a
`Permission:` record; only a person may write one here. `check_commands._NORMATIVE` refuses
every agent-initiated write to this key, so an agent that can ask is structurally unable to
approve. A grant here can never defeat a deny rule.

Grant the **narrowest** rule that unblocks the work. A list that only ever widens has
stopped being a control.

## The three rules that decide where a fact goes

Learned by getting each wrong, and each pinned by a test.

**1. A module names no location.** A stack ships `root: "."`; the project says where the
toolchain actually lives. Before this, one directory name appeared in four fields of one
module, kept in agreement by hand.

**2. Detection names the TOOLCHAIN, not the language.** `detect_any` takes the lockfile;
`detect_language` takes the manifest. A manifest is shared by every tool in an ecosystem,
so treating one as proof lets another tool's project validate clean and fail inside a
worker's worktree mid-wave. Matching a language marker and no toolchain marker is reported
**ambiguous** — a third answer, and the honest one.

**3. A command is a PROJECT fact wearing a toolchain's clothes.** `uv run` is a property of
uv; `pytest` is a choice you made. The module ships a default, `harness.yaml` overrides per
stack — and **overrides merge rather than replace**, so fixing one rotted command does not
silently drop the five beside it.

## Verifying it

```bash
harness/checks/check-project-config.sh   # schema, paths, and the archive invariant
harness/checks/check-stack-commands.sh   # probes each declared command, repairs what rotted
```
