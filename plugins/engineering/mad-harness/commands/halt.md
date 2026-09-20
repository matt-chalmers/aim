---
description: Stop a running swarm or campaign cleanly — pause the tasks in flight, or release them back to the queue, and leave no stuck state behind
argument-hint: "[pause|release] [optional: epic id or task ids]"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/harness/*), Bash(git:*), Read, Glob, Grep, AskUserQuestion, TaskStop
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

Halt the running swarm/campaign: **$ARGUMENTS** (default `pause`).

## First — how you actually stop the agents

**This command is post-interrupt cleanup.** Stopping the agents is a thing *you* do; `/halt`
tidies up after it. In order:

1. **`Esc`** — interrupts the main thread (the orchestrator). It stops dispatching new work.
   It does **not** necessarily reach subagents already running in the background.
2. **`/tasks`** — lists background work, including in-flight subagents, and lets you stop
   them individually. This is the reliable way to reach a worker that is mid-task.
3. Or ask in chat — "stop the swarm workers" — and I will `TaskStop` them by name.

**What each state means for the work:**

| Worker was… | Its work |
|---|---|
| past `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh slot-acquire` + commit | **landed** — it is a real commit, safe |
| mid-edit | **uncommitted, inside its own worktree** — writers are dispatched with `--worker <n>`, which creates the worktree, so their edits are NOT in your main checkout. `git status` in the main tree will look clean while real work sits stranded. |
| mid-verification (a lens) | nothing to clean — every lens, L1-L4, is read-only |

## 1. Assess before you touch anything

```bash
${CLAUDE_PLUGIN_ROOT}/harness/swarm/halt.sh assess        # reads only; changes nothing
```

One block: the main tree (usually clean — worker edits are not here), **every worktree's
uncommitted and unmerged state** (this is where a killed writer's work lives: writers are
dispatched with `--worker <n>`, so their edits are in `.claude/worktrees/`, invisible to
`git status` in the main tree — which is exactly how three worktrees went stranded and
unnoticed), what landed on `main`, **the claims with liveness** (`tk.sh claims` — the
authority on what is held; `list --status in_progress` finds tasks from other sessions, and
an operator following it literally cleaned up four foreign tasks and left the two real ones
claimed), the merge slot, the autosync state, and the open waves.

Report this before changing state. **Never discard uncommitted work without showing it
first** — a killed worker's edits are indistinguishable from your own until you look.

## 2. `pause` — keep the claims, stop the dispatch (the default)

Use when you want the same tasks resumed later, by you, in the state they are in.

```bash
${CLAUDE_PLUGIN_ROOT}/harness/swarm/halt.sh pause <epic-id>
```

**The claims are already the pause.** A claimed task does not appear in `tk.sh ready`, so
nothing will re-dispatch it; only *new* work needs stopping, which is `tk.sh park <epic>` —
the gate AND the status. The call then prints `resume-point.sh` for every claimed task (the
branch, the commit count, whether uncommitted work exists, whether a verdict was recorded —
that is the handover) and ends with §4. Leave the claims, leave the working tree, leave the
branches; if a partial edit does not compile, say so prominently in the report.

**To resume:** `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh unpark <epic-id>`, then re-run `/swarm` or
`/campaign`. **The resumed run adopts the work rather than redoing it:** `wave-plan.sh` asks
`resume-point.sh` for every task before dispatching, merges what was already verified,
verifies what was only committed, and re-attaches a worker (`dispatch.sh … --resume
<branch>`) to a worktree holding uncommitted changes. A worker with the same `BEADS_ACTOR`
re-claims its own task cleanly — `--claim` is idempotent for the existing holder.

## 3. `release` — hand the tasks back to the queue

Use when you want someone else, or the next run, to pick this work up fresh.

```bash
${CLAUDE_PLUGIN_ROOT}/harness/swarm/halt.sh release [<task-id>…]                       # default: every claim
${CLAUDE_PLUGIN_ROOT}/harness/swarm/halt.sh release [<task-id>…] --drop-uncommitted    # after reading `assess`
```

**First preserve. Then look. Then release. Then remove. In that order, because each step is
what makes the next one safe** — and the order is the code:

| step | rule |
|---|---|
| `preserve-worktrees.sh` | every worktree's uncommitted diff, untracked files and unmerged commits, as files under `.harness/halted-<date>/<branch>/`. **A failure here stops everything** — nothing is released and nothing removed |
| `resume-point.sh <id>` | per task: REATTACH / VERIFY / MERGE / FRESH — what a resumed run would adopt |
| `tk.sh release <id> --force` | the claim goes, the assignee is cleared, and it says so |
| `tk.sh update <id> --append-notes "released <date>: <state> on <branch>; work preserved at …"` | the path on the task, so nothing is lost whatever happens next |
| `git worktree remove --force` | **only** for REATTACH, **only** under `--drop-uncommitted`, and **only** when the preserve step reported that branch — never otherwise |

**The one decision is yours, per REATTACH task, from what `assess` showed:**

| it said | it means | do |
|---|---|---|
| `VERIFY` / `MERGE` | committed work on the branch | nothing — the branch is kept; the next run adopts it |
| `REATTACH` | uncommitted work in the worktree | **worth keeping → the default**: the worktree stays and the next run re-attaches. **Not worth keeping → `--drop-uncommitted`**: the work is preserved to files, then the worktree removed, so the next dispatch is `FRESH` instead of silently re-attaching whoever picks it up to a killed run's unverified edits |
| `FRESH` | nothing held | nothing; the empty branch is the sweep's to delete |

A released task with a worktree still holding unexplained edits is the worst outcome: the
next worker re-attaches to them, cannot tell whose they are, and builds on top.

## 4. Always — clear the stuck state

Both forms end with it: `tk.sh slot-check`, and `slot-release --force` **only when the holder
is provably gone** (an alive holder is a FAIL line, never forced — stop it with `/tasks`,
then run again; a slot held by a dead worker blocks the next wave forever, nothing times it
out); `git worktree prune`; then the sync tail — `export` (the jsonl is stale while
`export.auto` was off), the commit `chore(tracker): halt <epic> — <pause|release>`, pull,
push — and **`autosync on`**. `/swarm` and `/campaign` pre-flight set `export.auto` to
`false` so tasks cannot stage `issues.jsonl` into a sibling's commit; left off, `tk.sh close`
stops keeping the tracked jsonl fresh and your backlog quietly drifts from your code. It is
the last thing the call does.

## 5. Report


State exactly: which tasks landed, which are paused (and gated), which were released, what
uncommitted work remains and whose it was, merge-slot state, whether `export.auto` is
restored, and **the one command that resumes** — `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh unpark <epic>` or the `/campaign`
invocation. Someone returning tomorrow should not have to reconstruct any of it.
