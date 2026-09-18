---
description: Decompose a goal or epic into a reviewable task DAG, get it approved, and create it
argument-hint: "<goal text, or an epic id>"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/harness/*), Task, Bash(bd:*), Bash(git:*), Read, Glob, Grep, AskUserQuestion
---

<!-- ORCHESTRATOR CARD: mirrored from harness/orchestrator-card.md by
     check-orchestrator-card.sh --write; edit it there, never here. -->
**You are the most expensive caller in the system.** Measured: an orchestrator's context averaged ~210k tokens in its campaign, ~380k over its session; each tool call re-reads it, three to six times a worker's price. Four rules:

1. **Never load reference material into yourself.** A built-in agent (`Agent(subagent_type="general-purpose")`) loads it and answers; its whole return lands in your context, so ask for a few lines or a path. Measured: one reference skill loaded here cost $11.21 over 64 turns; ~$2 in a subagent.
2. **One call where five would do.** `preflight.sh`, `apply-plan.sh`, `close-epic.sh` are whole sequences; `scan.sh`, `peek.sh`, `run.sh` batch reads and runs; ask `tk.sh` once, `--json`.
3. **Artefacts by path.** `dispatch.sh … --digest`, `tk.sh note --file`: a plugin agent's result goes from its file to what consumes it, never through you.
4. **An hour idle, and the next request re-writes your whole context at the write rate.** Measured: four gaps re-wrote 3.5M tokens of one session, more than its campaign cost. Back at a large session, weigh its context against that, or start fresh.

Plugin agents run through `dispatch.sh`; a hook refuses them the Agent tool.
<!-- END ORCHESTRATOR CARD -->

Plan and decompose: **$ARGUMENTS**

## 1. Load context

`${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh prime`; `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh --readonly show <epic>`; the corpus index and the relevant feature folder
(`harness.yaml` → `paths.index`, `paths.features`).

## 2. Architecture gate — mandatory, with an explicit skip test

Dispatch the `architect` agent (and record its output per `/design` step 4) if **any** of
these is true:

- a new or changed model or migration
- a new rule in a core domain engine, or a change to how one computes
- a new adapter behind an existing extension point
- a new boundary between bounded contexts, or a new shared service
- a new API resource, or a change to an existing response shape
- you expect to cut more than ~5 tasks

Otherwise **state in one line which test failed, and skip.** This is a checklist rather
than "if it feels architectural" on purpose — a judgement call here gets skipped under
momentum, which is exactly how the agent ends up never being used. If the epic already
carries an `ARCHITECTURE:` note, reuse it rather than re-deriving it.

## 3. Dispatch the planner

**Dispatch the `planner` agent through the harness boundary now:**
`${CLAUDE_PLUGIN_ROOT}/harness/models/dispatch.sh planner --prompt-file <path>`. Naming it explicitly is what
selects its tier; the boundary is what makes that tier actually apply.

Carry in the prompt string: the goal, the epic's current children (`${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh --readonly show`),
any `ARCHITECTURE:` note, the lane-label vocabulary and concurrency caps, and the
instruction to produce a file-contention matrix. The prompt string is the only channel.

## 4. Review with the user

Render the planner's **DAG, contention matrix and wave plan verbatim** — do not summarise
the matrix, it is the part that prevents a pile-up.

`AskUserQuestion`: approve / approve with edits / re-plan / cancel. **Surface any
`decision` tasks first** — an unanswered decision blocks its whole subtree, and no worker
can ask the question later.

## 5. Create the tasks — from the main thread, not the planner

Run the `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh create` / `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh dep` lines one at a time, echoing each new id.

**Decision-record numbers are allocated here, by the main thread, at creation time** — list
the decisions directory (`harness.yaml` → `paths.adrs`), take the next number, and write it
into the task description. **Never leave a worker to pick one.** Two streams picking
independently has collided in practice, costing a renumber and stale references across code,
docs, tasks and memories.

Never use `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh create --graph`: `--dry-run` is silently ignored on that path, so a
malformed plan writes real tasks with no preview.

## 6. Validate and register

```bash
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh validate <epic-id>
# BEADS ONLY: molecules have no equivalent in other backends, and the harness does
# not require them. `tk.sh backend --json` reports whether yours has the capability.
bd swarm create <epic-id>      # optional: registers the molecule for `bd ready --mol`
```

Report waves, worker-sessions and max parallelism — **and restate that this number ignores
file contention.** It has rated an epic 11-wide when the file graph supported about two.

## 7. Hand off

Print the exact `/swarm <lane> <n>` to run, with `n` clamped to the lane cap.
