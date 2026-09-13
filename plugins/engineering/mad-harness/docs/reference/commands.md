# Commands

Ten slash commands. Each is a prompt in [`commands/`](../../commands/) that the plugin
loader installs; they are the whole user-facing surface.

## The pipeline, in order of use

<!-- GENERATED:commands — do not hand-edit; run harness/checks/check-docs.sh --write -->

| command | does |
|---|---|
| `/campaign-auto` | Iterate the open epic queue unattended — design, plan, swarm, verify, document and push each epic, self-approv |
| `/campaign` | Iterate the open epic queue — design, plan, swarm, verify, document and push each epic, asking you to approve  |
| `/decision` | Find the outstanding owner decision that unblocks the most work, verify its premise still holds, and resolve i |
| `/design` | Design the technical approach for a change before any tasks are cut or code is written |
| `/grind` | Autonomously work through unblocked tasks with rigorous testing, fidelity checks, live docs, and a commit + pu |
| `/halt` | Stop a running swarm or campaign cleanly — pause the tasks in flight, or release them back to the queue, and l |
| `/landit` | Conclude recent work — adversarially test, update docs, sync tasks, commit & push |
| `/plan-swarm` | Decompose a goal or epic into a reviewable task DAG, get it approved, and create it |
| `/requirements` | Take an outstanding REQUIREMENT task and specify it with the owner, interactively, until a planner could cut t |
| `/swarm` | Run one wave of parallel workers over the ready queue for a lane, with a serialized commit phase, a whole-repo |

<!-- /GENERATED:commands -->

## Running the queue

| command | notes |
|---|---|
| `/campaign` | iterates the epic queue, stopping at each epic boundary |
| `/campaign-auto` | the same, unattended |

## Interventions

| command | notes |
|---|---|
| `/decision` | finds the owner decision unblocking the most work and resolves it with you. Also where `Permission:` records are answered |
| `/halt` | stops a running swarm or campaign cleanly — pauses tasks in flight or releases them |

## Two things worth knowing

**`allowed-tools` in each command's frontmatter governs the interactive session**, not
dispatched workers. Workers are governed by [permissions](../concepts/permissions.md),
derived per agent.

**Commands invoke harness scripts by absolute path**, written `${CLAUDE_PLUGIN_ROOT}/harness/…`.
A command spelled with a shell variable cannot be permitted — permission rules match the
text before the shell expands anything — so the plugin loader's expansion is what makes
them runnable.
