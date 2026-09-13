# Architecture

Two halves, deliberately in different places.

<img src="../assets/architecture.svg" alt="The two halves: prompts read by a model, code executed by a machine, and harness.yaml carrying every project-specific fact">

| | where | is | fails how |
|---|---|---|---|
| **Prompts** | `agents/` `commands/` `skills/` | markdown read *by a model* | a lens catches it |
| **Code** | `harness/` | shell and Python executed *by a machine* | exits non-zero, a test catches it |
| **Config** | `harness.yaml` | every project-specific fact | `check-project-config.sh` catches it |

The split decides where anything new goes. If a machine can check it, it is code and it
gets a test. If it needs judgement, it is a prompt and it gets a lens.

## Agent, command, skill

Three layers, and the distinction is what keeps prompts small:

| | is |
|---|---|
| an **agent** | *who* does the work |
| a **command** | *what* to do now |
| a **skill** | *how* that kind of work is done well |

A skill is loaded on demand and declared per agent, so doctrine an agent never needs costs
it nothing. That is what makes supporting many technologies free for the projects not using
them — a test asserts that **adding a module leaves every agent's preload bill unchanged**.

## Where a rule belongs

Rules the harness owns are stated *by the harness*, in the three skills that between them
reach every agent, and pinned byte-identical by `check-conventions-mirror.sh`.

They were once cited from a consuming project's `CLAUDE.md`, on the reasonable argument
that duplicating it is pure cost. **That argument inverts once the rule is the harness's**:
then the project's copy is the duplicate, and the harness is treating its own doctrine as
someone else's authority. An audit found 20 of 26 such citations were exactly that.

A project's own conventions still live in the project's own file. The harness requires
nothing from it.

## What is deliberately not here

Application tooling. A script that brings up your real stack, or runs your end-to-end
suite, would exist with no agents involved — so it belongs with your application.

The test: **does it ship, or does it run the agents that build what ships?**

## Subsystems

| directory | owns |
|---|---|
| `models/` | model routing, the dispatch boundary, permissions, config, telemetry |
| `tracker/` | the four ports, two backends, graph, locks, events, render, archive |
| `verify/` | briefs, scoped runs, mutation, fidelity, batched reads and searches |
| `swarm/` | worktree lifecycle |
| `campaign/` | campaign telemetry |
| `checks/` | the mechanical gates |
| `stacks/` `frameworks/` | the two module axes |
| `wavelab/` | the live differential lab — two repos, two backends, real waves |
