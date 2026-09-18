---
description: Iterate the open epic queue — design, plan, swarm, verify, document and push each epic, asking you to approve every design and DAG
argument-hint: "[optional: an epic id, or a lane to bias toward]"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/harness/*), Task, Bash(git:*), Bash(make:*), Read, Glob, Grep, AskUserQuestion
---

<!-- ORCHESTRATOR CARD: mirrored from harness/orchestrator-card.md by
     check-orchestrator-card.sh --write; edit it there, never here. -->
**You are the most expensive caller in the system.** Measured: a campaign orchestrator's context averaged ~210k tokens during the campaign and ~380k over its session, so every tool call re-reads it — three to six times what the same call costs a worker. Three rules follow:

1. **Never load reference material into yourself.** A spec corpus, an API reference, a research body: a built-in agent (`Agent(subagent_type="general-purpose")`) loads it, answers your question, and dies with it. Measured: one reference skill loaded here cost $11.21 re-sent over the 64 turns that followed; the same load in a subagent, ~$2.
2. **One call where five would do.** `preflight.sh`, `apply-plan.sh`, `close-epic.sh` are whole sequences; `scan.sh`, `peek.sh`, `run.sh` batch reads and runs; ask `tk.sh` once with `--json`, not five times.
3. **Artefacts by path.** `dispatch.sh … --digest` and `tk.sh note --file`: a subagent's result goes from its file to whatever consumes it, never through you.

mad-harness agents run through `dispatch.sh` — a hook refuses the Agent tool for them; built-in agents are for delegated reading.
<!-- END ORCHESTRATOR CARD -->

Run a campaign over the open epic queue: **$ARGUMENTS** (empty = the whole queue).

**Follow the `campaign-loop` skill with `MODE=interactive`.** Read it now if it
is not already in your context — it is the whole procedure, and it is shared with
`/campaign-auto` so the two cannot drift.

`MODE=interactive` means:

- **The two analyst gates come to you as evidence, not as approvals to click through.**

- At **§3a**, before any design: the adequacy verdict (`ADEQUATE` / `INFERABLE` / `ABSENT`) and
  the `SPEC INDEX`. **Render every inference verbatim if `INFERABLE`** — an inference you never
  see is an invention with better manners, and correcting one costs you a sentence now against
  a lens round later. `ABSENT` does not reach you as a question: the epic parks and a
  `REQUIREMENT:` task is filed for `/requirements`.
- At **§3d**, before the plan is applied: the audit verdict. **Render every `blocking` finding
  verbatim.** These are tasks a verifier could not check or a planner could not cut correctly —
  approving past them is approving the ~36% first-pass rate.

**Neither is a rubber stamp.** You are the only reader who can say *"that inference is wrong"*
or *"that criterion is fine, ship it"*, and both gates exist because nothing else in the
pipeline reads the spec itself.

**§3b architecture gate** — render the architect's design **verbatim** and `AskUserQuestion`:
  accept / revise / reject. Do not create a single task until you have an answer.
- **§3c plan gate** — render the DAG, the file-contention matrix and the wave plan
  **verbatim**, and `AskUserQuestion`: approve / approve with edits / re-plan / cancel.
  Surface any `decision` tasks first.
- **§4 wave confirmation** — keep `/swarm`'s step-4 confirmation before each dispatch.

Everything else is exactly the skill: triage, waves, the lenses with unanimity (L1-L3 always,
L4 `verifier-security` on trigger), the wave-stage `/code-review`, whole-repo
wave gate, push after every wave, circuit breakers, and the epic close-out that will not let
an epic close without tests and docs.

**This is the version to use for an epic you have not seen a design for yet** — above all
one that changes a contract other work is already built against, where the design decision is
larger than any single task in it.

If you want it to run unattended and self-approve, that is `/campaign-auto`.
