---
description: Design the technical approach for a change as a debate — an architect and an analyst, run as agent-teams teammates, argue the design before it is staged. Interactive only; the experiment measured against /design.
argument-hint: "<goal text, or a task/epic id>"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/harness/*), Task, Agent, SendMessage, Read, Write, Glob, Grep, AskUserQuestion
---

<!-- ORCHESTRATOR CARD: mirrored from harness/orchestrator-card.md by
     check-orchestrator-card.sh --write; edit it there, never here. -->
**You are the most expensive caller in the system.** Measured: an orchestrator's context averaged ~210k tokens in its campaign, ~380k over its session; each tool call re-reads it, three to six times a worker's price. Four rules:

1. **Never load reference material into yourself.** A built-in agent (`Agent(subagent_type="general-purpose")`) loads it and answers; its whole return lands in your context, so ask for a few lines or a path. Measured: one reference skill loaded here cost $11.21 over 64 turns; ~$2 in a subagent.
2. **One call where five would do.** Every `swarm/*.sh` is a whole sequence (preflight, apply-plan, close-wave, close-epic); `scan.sh`, `peek.sh`, `run.sh` batch; `tk.sh` once, `--json`.
3. **Artefacts by path.** `dispatch.sh … --digest`, `tk.sh note --file`: a plugin agent's result goes from its file to what consumes it, never through you.
4. **An hour idle, and the next request re-writes your whole context at the write rate.** Measured: four gaps re-wrote 3.5M tokens of one session, more than its campaign cost. Back at a large session, weigh its context against that, or start fresh.

Plugin agents run through `dispatch.sh`; a hook refuses them the Agent tool.
<!-- END ORCHESTRATOR CARD -->

Design the approach for: **$ARGUMENTS** — as a debate.

**This is an experiment, and it is interactive only.** It needs Claude Code's agent teams
(`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`) and the harness's opt-in for this one use
(`MAD_HARNESS_TEAMS_DEBATE=1`), both in the environment you were started with. If either is
missing, say so in one line and run `/design` instead — do not attempt a spawn; the guard hook
refuses it.

Why a debate, and why only here: a teammate's output reaches you only by message, never as a
result landing in your context — which is the measured reason plugin agents are otherwise kept
off the Agent tool (67.2M prompt tokens through it against 6.9M through the dispatcher). What a
teammate still lacks is everything else the dispatcher provides: a tier's effort (teammates
inherit yours), a cost ceiling, a sandbox, a cost record, and its doctrine (a teammate loads its
definition's tools and model, not its skills). Those are accepted here because a person is
present, the rounds are capped, and the doctrine is handed over by file. The measurement this
command exists for is in `docs/upgrading.md` § 0.10.31.

## 1. Load context

Exactly as `/design` §1: `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh prime`, then
`${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh show <id>` if `$ARGUMENTS` names a record, then the
corpus index → the feature doc → its decision records → the architecture doc, all from
`harness.yaml` → `paths`. Keep the paths; you hand them over, you do not read them aloud.

## 2. Doctrine, by file

```bash
${CLAUDE_PLUGIN_ROOT}/harness/swarm/doctrine.sh architect      # → .harness/run/doctrine/architect.md
${CLAUDE_PLUGIN_ROOT}/harness/swarm/doctrine.sh analyst        # → .harness/run/doctrine/analyst.md
```

Each prints the path of the exact text the dispatcher would have put in that agent's system
prompt. A teammate does not load it on its own; the spawn prompt tells it to read the file first.

## 3. Spawn the two teammates

Spawn them from the plugin's own definitions — `mad-harness:architect` and `mad-harness:analyst`
— as **teammates**, not subagents. Each spawn prompt carries, in this order:

1. `Read <its doctrine path> first — it is your doctrine, the same text the dispatcher would have
   put in your system prompt.`
2. The record: the full `tk.sh show` text (or the goal text), and the paths from §1 — the spec
   index, the feature doc, the decision records, the architecture doc. Paths, not contents.
3. The size of the epic — its open task count, from the record — and that the deliverable is in
   proportion: a paragraph per task at most, sections only where there is something to decide.
4. The protocol:
   - **architect**: draft the design under your normal contract (`ARCHITECTURE:` block first;
     `ADEQUACY: ABSENT` and stop if you cannot design without inventing scope; every open
     question on its own `DECISION:` line). Send the draft to the analyst. On each round of
     findings, revise and send again. When the analyst answers `VERDICT: PASS`, or after three
     rounds, send the final design to the lead as your final answer, whole.
   - **analyst**: audit each draft against your standard (your AUDIT contract: `VERDICT: PASS`
     or `VERDICT: FAIL` first, then findings tagged blocking or filed; a gap written down is a
     PASS, the same gap silent is a FAIL). Reply to the architect. On PASS, tell the lead.
   - **both**: every Bash call is one plain command — no `;`, `&&`, `|`, `echo`, `python3 -c`;
     never a tracker write verb; nothing you write on disk, ever — you have no Write tool and the
     lead records the outcome.

Then wait on messages. Do not read the corpus yourself while you wait; the teammates have it.

## 4. Record what came back

You have `Write`; they do not. On the architect's final answer:

- write it, verbatim, to `.harness/run/plan/<id>/design-debate.md`
- write the analyst's last audit to `.harness/run/plan/<id>/audit-debate.md`
- shut both teammates down — an idle teammate keeps consuming until it exits

## 5. Review, stage, hand off

Exactly `/design` §3, §4 and §5 — render the design verbatim, `AskUserQuestion` (accept, revise,
reject; every `DECISION:` line presented as a choice and never answered by you), then on
acceptance:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/swarm/stage-design.sh <id> .harness/run/plan/<id>/design-debate.md
```

and print the `/plan-swarm <id>` invocation.

## 6. The measurement — what to note before you close

This command is measured against `/design` followed by one `dispatch.sh analyst` audit of the
staged design — two dispatches, no dialogue — on the same epic. A teammate's cost folds into
your session, so the comparable number is **this session's growth**:
`${CLAUDE_PLUGIN_ROOT}/harness/checks/session-cost.sh <your transcript>` before and after.
Note also: wall-clock, the number of rounds, the analyst's final verdict and its blocking count,
and the `DECISION:` lines the design raised. Put them in the 0.10.31 note. One run per arm is
direction, not size.
