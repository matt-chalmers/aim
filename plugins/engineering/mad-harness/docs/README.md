# Documentation

A multi-agent development harness for Claude Code. Machinery is shared; everything specific
to a repository lives in one `harness.yaml` that repository writes.

Running it: [Getting started](getting-started.md). Evaluating it first:
[Architecture](concepts/architecture.md) and [The loop](concepts/the-loop.md).

## By intent

**I want to run it**
| page | answers |
|---|---|
| [Getting started](getting-started.md) | install, configure, and land a first wave |
| [Requirements](requirements.md) | what must be on the machine, why, and how to install it |
| [Troubleshooting](guides/troubleshooting.md) | a symptom, and what actually causes it |

**I want to understand it**
| page | answers |
|---|---|
| [Architecture](concepts/architecture.md) | the two halves — prompts and code — and where a new rule belongs |
| [The loop](concepts/the-loop.md) | how a requirement becomes merged code |
| [Agents and tiers](concepts/agents-and-tiers.md) | who does the work, on which model, at what cost |
| [Tasks and tracking](concepts/tasks-and-tracking.md) | records, the DAG, waves, and swappable backends |
| [Verification](concepts/verification.md) | four lenses, why they must not see the same evidence |
| [Permissions](concepts/permissions.md) | the sandbox, the allowlist, and the operator queue |

**I want to look something up**
| page | answers |
|---|---|
| [Commands](reference/commands.md) | the ten slash commands |
| [harness.yaml](reference/harness-yaml.md) | every configuration block |
| [Agents](reference/agents.md) | the twelve agents |
| [Skills](reference/skills.md) | the thirteen skills |
| [Stacks](reference/stacks.md) | the toolchain module schema |
| [Checks](reference/checks.md) | the sixteen mechanical gates |
| [Scripts](reference/scripts.md) | shell entry points |
| [Tracker ports](reference/tracker-ports.md) | the four ports and the conformance contract |
| [Dispatch](reference/dispatch.md) | `Resolved`/`Outcome`, tier resolution, injected context, telemetry |
| [Wave lifecycle](reference/swarm.md) | the ten steps of /swarm, its feedback paths, and what blocks what |
| [Workers](reference/workers.md) | worktree isolation, shared state, `.swarm-env`, concurrency primitives |

**I want to change it**
| page | answers |
|---|---|
| [Customising](guides/customising.md) | fit it to your repository without forking |
| [Contributing](guides/contributing.md) | add an agent, skill, stack, command, check or metric |

## Conventions

**Each page owns facts nothing else states.** Two documents disagreeing is this corpus's
most expensive defect class, so pages link rather than restate. Where doctrine lives in a
skill, the page points at the skill — a summary is a second source and it drifts.

**Enumerable tables are generated** from frontmatter and disk by
`harness/checks/check-docs.sh`, and `make check` fails on drift. Do not hand-edit anything
between `GENERATED:` markers.

**`docs/llms.txt`** is the machine-readable index, for agents that need to find one page
rather than read twenty.
