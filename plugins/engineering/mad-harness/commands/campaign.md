---
description: Iterate the open epic queue — design, plan, swarm, verify, document and push each epic, asking you to approve every design and DAG
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
