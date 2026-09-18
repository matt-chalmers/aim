---
description: Design the technical approach for a change before any tasks are cut or code is written
argument-hint: "<goal text, or a task/epic id>"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/harness/*), Task, Read, Glob, Grep, AskUserQuestion
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

Design the approach for: **$ARGUMENTS**

## 1. Load context

```bash
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh prime
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh show <id>        # if $ARGUMENTS is a task or epic id
```

Read the corpus index → the relevant feature doc → every decision record it cites in its
frontmatter → the architecture doc for the subsystem touched. All four locations come from `harness.yaml` → `paths`.

## 2. Dispatch the architect

**Dispatch the `architect` agent through the harness boundary now:**
`${CLAUDE_PLUGIN_ROOT}/harness/models/dispatch.sh architect --prompt-file <path>` — the tier decides its model,
effort and budget. Name it explicitly — this
instruction is the request, so do not skip it on the grounds that you could design it
yourself.

Give it, in the prompt string: the goal, the task's full `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh show` text if there is one,
the feature docs and ADRs you identified, and any existing `ARCHITECTURE:` note. **The
prompt string is the only channel** — the agent sees none of this conversation.

## 3. Review with the user

Render the design **verbatim**. Then `AskUserQuestion`:

- accept as designed / revise / reject and re-design
- and, separately, settle any `decision` the architect surfaced — present its options and
  trade-offs as the choices

Never accept a design on the user's behalf, and never answer a `decision` yourself.

## 4. Record it where a worker can actually see it

Workers inherit no conversation history, so a design that lives only here evaporates.
On acceptance, write it to **`<paths.proposed>/<epic-id>-<slug>/design.md`** from that directory's design template, and record a pointer on the task from the main thread:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh update <id> --append-notes "ARCHITECTURE: design staged at <paths.proposed>/<epic-id>-<slug>/design.md — <one-line approach>"
```

Use `--append-notes`. **Do not use `--design`** — that field is write-only, absent from
both `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh show` and `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh show --json`, and set on almost no tasks in practice. `notes` round-trips.

**The note is the pointer; the file carries the content.** A design recorded only as a task
note disappears when the epic closes, taking the reasoning every later reader needs with it.
The file folds in at **fold-in ②** — `campaign-loop` §5 — routed by content (a non-obvious
choice to an ADR, a reusable mechanism to an architecture doc, a changed contract to the
feature doc) and is deleted then. The proposed directory must be empty for the epic before it
closes. See the `spec-lifecycle` skill.

Every decision this design raises — settled or not — gets a row in the epic's
`<paths.proposed>/<epic-id>-<slug>/decisions.md`, from that directory's decisions template.
**Whoever later closes that task updates the register in the same action**, and
`${CLAUDE_PLUGIN_ROOT}/harness/checks/check-decision-register.sh` fails if they diverge. Include decisions settled *before*
this epic existed where they bind it: that is exactly the context the next reader cannot
reconstruct, and `analyst-survey` treats a closed decision as settled, not irrelevant.

If it surfaced a decision the owner has not settled, open a **draft decision record** at
`<paths.proposed>/<epic-id>-<slug>/adr-<slug>.md` from that directory's draft template, paired
with the `decision` task. **Nothing may cite a draft as settled** — it has no number until the
owner decides, and on resolution it is `git mv`d into `paths.adrs`.
If it surfaced a decision the user did not settle, file `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh create -t decision` and make
the dependent work depend on it.

## 5. Hand off

Print the exact `/plan-swarm <id>` invocation to decompose it.
