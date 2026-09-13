# MAD harness

A multi-agent development harness for Claude Code: model tiering and dispatch, parallel
worker worktrees, decorrelated verification lenses, and the doctrine those agents run on.

Everything here is machinery. Everything about *your* repository lives in one file you
write — `harness.yaml` — plus optional stack modules. **That split is the whole design: the
harness is shared, the facts are not.**

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

**Full walkthrough: [docs/getting-started.md](docs/getting-started.md).**
**Prerequisites: [docs/requirements.md](docs/requirements.md)** — note that dispatch
requires a sandbox and refuses to run without one.

## What it does

```
requirement ──► spec ──► design ──► task DAG ──► wave ──► verify ──► fold-in
```

**Model tiering.** `worker` / `strong` / `strategic` map to concrete models. An agent
declares the tier its work needs, never a model. Changing provider is one edit, and
high-risk work is forced up regardless of an agent's default.

**A dispatch boundary.** Every agent runs through the Claude Agent SDK with a per-dispatch
model, effort, hard budget ceiling and real token accounting — then the cost series is read
back, so routing is measured rather than argued.

**Isolated parallel workers.** One git worktree per worker, dependencies restored per the
stack module, a per-worker database so parallel workers cannot corrupt each other's
fixtures — and OS-level containment around each.

**Decorrelated verification.** Four lenses that stack *because each sees something
different*. The spec lens never sees the diff; that independence is the point, and it has
caught defects the diff-reading lens passed.

**A pluggable tracker.** `beads` or markdown records, behind one contract of 54 tests run
against the real binaries. A live differential proves both backends reach the same place
from the same epic.

**Config that repairs itself.** Which command runs your tests is a fact about your project,
and configs rot. At pre-flight the harness probes each declared command, derives a
replacement from what your repo already declares, proves it by running it, and records it.
It repairs `commands` and nothing else — your security surface and coverage bar are yours.

**Two module axes.** A *stack* says how to run things; a *framework* says how to write good
code. Independent, so one toolchain serves many frameworks. Adding either is a YAML file —
never a code change, and never an agent change.

## Documentation

**[docs/](docs/README.md)** — the index.

| | |
|---|---|
| [Getting started](docs/getting-started.md) | install → configure → first wave |
| [Concepts](docs/concepts/) | the loop, agents and tiers, tasks, verification, permissions |
| [Reference](docs/reference/) | commands, `harness.yaml`, agents, skills, stacks, checks, scripts, ports |
| [Customising](docs/guides/customising.md) | fit it to your repo without forking |
| [Contributing](docs/guides/contributing.md) | add an agent, skill, stack, command, check or backend |
| [Troubleshooting](docs/guides/troubleshooting.md) | symptoms and their real causes |

Working on the harness itself: [CLAUDE.md](CLAUDE.md).

## Layout

| path | is |
|---|---|
| `commands/` `agents/` `skills/` | the prompt surface the plugin installs |
| `harness/` | the code: routing, dispatch, tracker, verification, checks |
| `templates/` | what a consuming project copies |
| `docs/` | the documentation |

## Status

Extracted from a working repository where it has run real campaigns. Portable, but young:
exercised end to end against a small number of projects so far. Expect the stack module set
to grow — that is the designed extension point.
