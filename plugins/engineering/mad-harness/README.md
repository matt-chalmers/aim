# MAD harness

A multi-agent development harness for Claude Code. It takes a requirement to merged,
verified code: specify, design, decompose into a dependency graph, run isolated workers in
parallel, judge the result through four independent lenses, and fold what was learned back
into the documentation.

Everything here is machinery. Everything about *your* repository lives in one file you
write — `harness.yaml` — plus optional stack modules. **That split is the whole design: the
harness is shared, the facts are not.**

```
requirement ──► spec ──► design ──► task DAG ──► wave ──► verify ──► fold-in
```

## Install

```bash
claude plugin marketplace add matt-chalmers/aim   # or a local clone path
claude plugin install mad-harness@aim
```

Then, in the repository you want it to work on:

```
/harness-setup
```

That skill writes `harness.yaml` with you. It reads what it can from the repo first, so it
asks about the gaps rather than handing you a questionnaire.

**Walkthrough: [docs/getting-started.md](docs/getting-started.md) · Prerequisites:
[docs/requirements.md](docs/requirements.md)** — note that dispatch requires an OS sandbox
and refuses to run without one.

## Is this for you?

**It fits** a repository with a real test suite, where work can be decomposed into tasks
that touch disjoint files, and where you want agents running unattended against your default
branch without that being reckless.

**It does not fit** a codebase with no automated tests — the verification lenses re-run and
judge a suite, and with nothing to run they degrade to opinion. Nor a task that is one
indivisible edit: the parallel machinery is overhead you would pay for nothing.

**Check your toolchain first.** The harness has to know how to restore dependencies in a
fresh worktree and how to run your suite, and that comes from a *stack module*. Two ship
today — `python-uv` and `node-npm` (plus `django` and `nextjs` on the framework axis). If
yours is not one of them you are not blocked: a module is a YAML file with no code in it,
living in your own repository. But it is an hour's work before your first wave rather than
after, so know it going in. [The current list and the schema](docs/reference/stacks.md).

**The honest status.** Extracted from a working repository where it has run real campaigns.
Portable, but young: exercised end to end against a small number of projects so far. Expect
the stack module set to grow — that is the designed extension point.

## What it does

### Model tiering and a dispatch boundary

`worker` / `strong` / `strategic` map to concrete models. An agent declares the tier its
work needs, never a model, so changing provider is one edit and high-risk work is forced up
regardless of an agent's default. Every agent runs through the Agent SDK with a per-dispatch
model, effort, ceiling, sandbox, permission profile and doctrine — and one telemetry event.
A hook refuses the Agent tool for the plugin's own agents, so there is no second path an
agent can run on — no dispatch without a ceiling, a sandbox and a cost record.

→ [Agents and tiers](docs/concepts/agents-and-tiers.md) · [Dispatch](docs/reference/dispatch.md)

### Any provider, priced honestly

Any tier can be routed off Anthropic. Three things that endpoint compatibility does *not*
carry are handled explicitly rather than assumed: a six-probe gate proves the provider can
actually sustain a tool loop; a declared price block replaces the CLI's own table, which
priced a third-party model at **10× the real cost** while looking entirely plausible; and
`billing` says which pocket a dispatch spends from, so a plan's allowance and an invoice are
reported apart and never summed.

→ [Providers and pricing](docs/concepts/providers.md)

### Ceilings that mean what they say

`max_budget_usd` is enforced against what the dispatch actually costs. On a tier the harness
prices, it meters the stream itself — the CLI is given no ceiling, because a threshold in a
currency nobody can state is not a bound. A provider that reports no usage is stopped rather
than run uncapped.

→ [Cost § Ceilings](docs/concepts/cost.md#ceilings)

### Isolated parallel workers

One git worktree per worker, dependencies restored per the stack module, a per-worker
database so parallel workers cannot corrupt each other's fixtures, an `O_EXCL` claim so a
double-dispatch loses rather than corrupts, and OS-level containment around each. Merges are
serialised through one slot; the whole-repo gate runs **once**, on the merged tree.

→ [Workers](docs/reference/workers.md) · [Wave lifecycle](docs/reference/swarm.md)

### Decorrelated verification

Four lenses that stack *because each sees something different*: correctness, test quality,
spec and blast radius, and — on trigger — security. The spec lens is never shown the diff;
that independence is the point, and it has caught defects the diff-reading lens passed. Any
FAIL blocks. A result with no verdict line is "could not judge", never a pass.

→ [Verification](docs/concepts/verification.md)

### A pluggable tracker

`beads` or plain markdown records, behind one port contract — so the choice is yours and
reversible, and `tk.sh migrate` moves every record and edge between them. Markdown needs no
install and diffs like code; beads gives you a query surface.

Either way the harness schedules from a **graph**, not a list, so it can tell you before a
wave whether your decomposition is genuinely parallel or a chain wearing a DAG's clothes.

→ [Tasks and tracking](docs/concepts/tasks-and-tracking.md) · [Tracker ports](docs/reference/tracker-ports.md)

### Config that repairs itself

Which command runs your tests is a fact about your project, and configs rot. At pre-flight
the harness probes each declared command, derives a replacement from what your repo already
declares, proves it by running it, and records it. It repairs `commands` and nothing else —
your security surface and coverage bar are yours.

→ [harness.yaml](docs/reference/harness-yaml.md)

### Two module axes

A *stack* says how to run things; a *framework* says how to write good code. Independent, so
one toolchain serves many frameworks without an N×M matrix, and teaching the harness a new
one is a YAML file — never a code change, and never an agent change. Modules your project
does not use cost it nothing: an agent loads only the doctrine it declares.

Four ship, listed above. This is the growth axis, and the one place the project is most
obviously young.

→ [Stacks](docs/reference/stacks.md) · [Customising](docs/guides/customising.md)

### Tuning you can audit

The defaults you inherit are not folklore. Each one that affects cost or quality is a
switch, recorded on every dispatch, and it was set by an A/B run against a seeded epic —
with the numbers in the release note that changed it, including the interquartile ranges, so
you can see whether the difference was real or noise.

That matters when a default is wrong for *your* repository, which some will be: you can find
what it was bought with, re-run the same comparison against your own code, and change it in
`harness.yaml` with evidence rather than a guess. The lab that produces those numbers ships
with the harness.

→ [Measurement](docs/guides/measurement.md) · [Cost](docs/concepts/cost.md)

## Requirements

| | |
|---|---|
| Claude Code | with an OS sandbox available — dispatch refuses to run without one |
| a tracker | `beads`, or the built-in markdown backend with no install |
| a test suite | the lenses re-run and judge it; without one they degrade to opinion |
| Python + `uv` | for the harness's own code |

Full list, with why each is needed: [docs/requirements.md](docs/requirements.md).

## Documentation

**[docs/](docs/README.md)** — the index. `docs/llms.txt` is the machine-readable version.

| | |
|---|---|
| [Getting started](docs/getting-started.md) | install → configure → first wave |
| [Glossary](docs/glossary.md) | stack, lens, wave, lane, tier, pocket — every term, and where it is defined |
| [Concepts](docs/concepts/) | the loop, agents and tiers, tasks, verification, permissions, cost, providers |
| [Reference](docs/reference/) | commands, `harness.yaml`, agents, skills, stacks, checks, scripts, ports, dispatch |
| [Customising](docs/guides/customising.md) | fit it to your repo without forking |
| [Measurement](docs/guides/measurement.md) | how a default is allowed to move |
| [Contributing](docs/guides/contributing.md) | add an agent, skill, stack, command, check, lever, hook or backend |
| [Troubleshooting](docs/guides/troubleshooting.md) | symptoms and their real causes |

Working on the harness itself: [CLAUDE.md](CLAUDE.md).

## Layout

| path | is |
|---|---|
| `commands/` `agents/` `skills/` | the prompt surface the plugin installs |
| `harness/` | the code: routing, dispatch, tracker, verification, checks |
| `harness/wavelab/` | the live differential lab and the A/B rig |
| `templates/` | what a consuming project copies |
| `docs/` | the documentation |
