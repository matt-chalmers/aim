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
| `declined` | modules present here that you deliberately do not use, `stacks:` / `frameworks:` keyed `name@root`, each with a reason — answers the config check's "present but not declared" warning for that root only, and is printed on every check. Normative: no agent may write it | no |
| `tracker` | which backend, and where its records live | no (defaults to `beads`) |
| `beads` | the id prefix the task-hygiene checks build their patterns from | yes, on the beads backend |
| `swarm` | wave sizing and worker resources | no |
| `ports` | every TCP port your servers bind, by name — the pre-flight probes them | no |
| `dispatch` | cost levers, each a measured switch: `cache_ttl`, `static_prefix`, `stagger_seconds`, `task_budget_tokens`, `lean_catalog`, `plan_tiers` — see [`models/levers.py`](../../harness/models/levers.py) | no (`task_budget_tokens` defaults from the activity, `lean_catalog` and `plan_tiers` on; the rest off) |
| `strengths` | the model axis, patched per key over the plugin's: `provider`, `model`, `thinking` — see [Model config](#model-config) | no |
| `activities` | the work axis, patched per key and per complexity bucket: which agent, which strengths, what it may spend — see [Model config](#model-config) | no |
| `providers` | the provider set: endpoint, credential, `billing`, and each model's `price` — see [Model config](#model-config) | no |
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

Three blocks decide what runs a piece of work and what that work is worth. They are kept
apart because conflating them is how a project ends up unable to change a model without
editing an agent — and because a ceiling and an ordering were never facts about an engine.

| block | owns | shape |
|---|---|---|
| `strengths` | **what runs the work** — provider, model, thinking, and nothing else | patched per name, then per key |
| `activities` | **what the work is** — its agent, its chain, its ceiling, its told budget | patched per id, then per complexity bucket, then per key |
| `providers` | how a provider is reached, which pocket pays, and what its models cost | patched per name; `env` per key and `models` per model id |

**Patch, do not replace.** Keys you leave out stay the plugin's, so an upgrade that retunes a
ceiling still reaches you. A project uses the **same blocks the plugin ships** — there is no
parallel project-only block, which is why "plugin or project" is provenance
(`strength_source`) rather than a precedence rank. `model` and `provider` move together:
naming one without the other is refused rather than silently half-applied. A `strengths` chain
is a list and replaces wholesale — merging two orderings per index would route escalation
somewhere nobody chose.

```yaml
strengths:
  cheap:
    provider: openrouter
    model: qwen/qwen3-coder-plus     # a concrete id, never an alias
    thinking: medium

activities:
  work.implement:
    strengths: [cheap, strong]       # the chain; its head runs, the rest are where ESCALATE goes
    max_budget_usd: 6.00             # what THIS WORK is worth, whatever model runs it
    task_budget_tokens: 600000
    complex:                         # a bucket overrides only what it restates
      strengths: [strong]
      max_budget_usd: 10.00

providers:
  anthropic:
    billing: subscription            # a plan's allowance; `metered` is the default
  openrouter:
    env:
      ANTHROPIC_BASE_URL: https://openrouter.ai/api
      ANTHROPIC_AUTH_TOKEN: ${OPENROUTER_API_KEY}
    models:
      qwen/qwen3-coder-plus:
        price:                       # REQUIRED off Anthropic
          input_per_mtok: 1.00
          output_per_mtok: 5.00
```

**Resolution is per field, specificity wins.** For an activity at a complexity: the bucket's
value, else the activity's. `strengths` is **mandatory** — a dispatch that cannot resolve one
is refused, because there is no sane default for which model runs work — and it is checked
statically, so an activity covering two of the three readings fails the config check rather
than failing mid-wave on the third. The two budgets are optional and warn at config time; an
absent ceiling records `ceiling_source: unset`, distinct from one that cannot be checked.

Four rules this block is checked against, each by `check-project-config.sh`:

1. **A credential is a `${VAR}` reference, never a value.** `harness.yaml` is committed. The
   reference resolves from the environment (or `harness/.env`) at dispatch time and never
   reaches a record, a task or telemetry — `env_names` carries names only. `TOKEN`, `KEY` and
   `SECRET` in a variable name all trigger it.
2. **A model reached off Anthropic declares a `price`**, under its provider — a rate is a fact
   about a model, not about a role, and two strengths on one model would otherwise state it
   twice and be free to disagree. See [providers](../concepts/providers.md).
3. **`model:` is a concrete id, never an alias.** `opus` resolved to two different model
   generations on two dispatch paths in the same session, which invalidated a whole parity
   experiment before anyone noticed.
4. **A strength no activity can reach is reported**, and so is a pair of strengths that share
   a model off Anthropic and differ only in `thinking` — measured on one provider, thinking
   moved nothing outside the noise, so such a pair may cost the same and deliver the same.

Precedence, and what each dispatch records about the decision:
[dispatch](dispatch.md#strength-resolution). The concepts:
[agents, activities and strengths](../concepts/agents-and-activities.md).

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

## The setup ledger

`/harness-setup` records which blocks a person has reviewed in `.harness/setup.json`, tracked
beside the config. **It holds no config values** — only, per block, a state (`confirmed`,
`declined`, `repaired`, or `defaulted`), the plugin version it was reviewed at, the date, a hash
of the block's parsed value, and an optional reason — so it can never disagree with
`harness.yaml` about what the config says, only about whether anyone looked.

- The hash is over the parsed value, so a comment or reformatting changes nothing; a changed
  value on a confirmed block is reported by the config check as *your edit, unconfirmed*.
- A block with no entry is owed — which is how an interrupted setup resumes, and why a config
  from before the ledger is walked once in full.
- The pre-flight's command repair records `repaired` on the commands block: a machine's change,
  visible, and never a demotion.
- Confirming `frameworks` also stores each root's direct dependency **names**; the config check
  warns when one appears that was not there, since a new framework arrives that way.

Commit it with the config. Absent, nothing warns: the config check never depends on it.

## Verifying it

```bash
harness/checks/check-project-config.sh   # schema, paths, and the archive invariant
harness/checks/check-stack-commands.sh   # probes each declared command, repairs what rotted
```
