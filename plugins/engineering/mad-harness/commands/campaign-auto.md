---
description: Iterate the open epic queue unattended — design, plan, swarm, verify, document and push each epic, self-approving designs and DAGs
argument-hint: "[optional: an epic id, or a lane to bias toward]"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/harness/*), Task, Bash(git:*), Bash(make:*), Read, Glob, Grep, AskUserQuestion
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

Run an **unattended** campaign over the open epic queue: **$ARGUMENTS** (empty = the whole
queue).

**Follow the `campaign-loop` skill with `MODE=auto`.** Read it now if it is not
already in your context — it is the whole procedure, and it is shared with `/campaign` so the
two cannot drift.

`MODE=auto` means you approve your own designs and DAGs and keep going. That is the point,
and it is also the risk — and there is a cheaper way to run it across many epics: the
session you are in carries every finished epic's context into the next one, re-read on
every request, while `${CLAUDE_PLUGIN_ROOT}/harness/swarm/campaign.sh` runs each epic in
a fresh headless session through the dispatcher. Use this command for one epic, or a
few; use the script for the queue. The risk below is the same either way: **the architecture you accept is what every worker then follows for
the rest of the epic.** So the guardrails below are not optional.

## What you must still never do

**Never answer a `decision` task** — not once, not "obviously", not to keep the run moving.
A genuine product, spec or design question gets filed and the epic gets parked:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh create "<the question>" -t decision -p 1 --description "<options and trade-offs>"
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh gate create <epic-id> --reason "<what decision is owed>"
```

Then move to the next epic. Spec and design calls belong to the owner, and
nothing in this mode changes that. **If the architect returns an open question of any kind,
that is a decision record** — do not resolve it yourself because you happen to have an opinion.

**And if the architect returns a specification-adequacy verdict of `ABSENT`, that is a
REQUIREMENT task and the epic parks — do not design it.** Auto-accept covers **design**,
never **invented scope**. An epic with no children and no acceptance criteria will
otherwise get an architecture you auto-accept, and every worker then follows it for the
rest of the epic.

Likewise: never silently override a design handover, and never dumb down a spec to match the
code. Both are parking conditions.

## Extra obligations this mode carries

- **Mark every auto-accepted design.** Record `AUTO-ACCEPTED` in the epic's `ARCHITECTURE:`
  note and list them prominently in the final report. The owner needs to know exactly which
  architecture landed without review.
- **Run the §3d on every epic.** `analyst` reads the planner's task set independently
  before you approve it. Self-approving a plan is exactly when an outside read of its quality
  is worth most — the risk this mode carries is that the DAG you accept is what every worker
  then follows. A second consecutive audit FAIL means the epic is underspecified: file a
  `REQUIREMENT:` task and park it.
- **Skip the per-wave confirmation** (§4 step 2) — the plan was approved at 3c. Keep every
  other `/swarm` step, especially the contention re-check, the lenses (including **L4
`verifier-security` whenever its trigger fires**), and the wave-stage `/code-review` at step 8b.
- **Honour every circuit breaker** in §4 step 4, and stop the whole run after **three
  consecutively gated epics** — that pattern means something systemic is wrong and continuing
  just burns work.
- **Push after every wave** (§4 step 3) so an unattended run never strands work locally.

## Final report

Lead with the three things that need human eyes: **which designs were auto-accepted**,
**which epics are gated on a DECISION and what each is owed**, and **which are gated on a
MISSING REQUIREMENT** — an epic too underspecified to design against without inventing
scope. Keep those last two counts apart: a decision is a fork the owner picks between, a
requirement gap is an absence only the owner can fill, and the second is the number that
predicts the next run's first-pass rate. Then epics completed, tasks closed,
and the four health signals.
