---
description: Stop a running swarm or campaign cleanly — pause the tasks in flight, or release them back to the queue, and leave no stuck state behind
argument-hint: "[pause|release] [optional: epic id or task ids]"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/harness/*), Bash(git:*), Read, Glob, Grep, AskUserQuestion, TaskStop
---

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
git status --porcelain                 # the MAIN tree — usually clean; worker edits are not here
git worktree list                      # every worker worktree, and the branch each is on
git worktree list --porcelain | awk '/^worktree /{print $2}' | while read -r w; do
  echo "== $w"; git -C "$w" status --porcelain; git -C "$w" log --oneline main..HEAD
done                                   # THIS is where uncommitted and unmerged work lives
git log --oneline -10                  # what actually landed on main
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh list --status in_progress --json    # tasks still claimed
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh slot-check                    # free, or held by a worker that died?
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh backend --json    # check the autosync state           # almost certainly export.auto=false
```

**Look in the worktrees, not the main checkout.** A killed writer leaves both uncommitted
edits *and* possibly a committed-but-unmerged branch inside its own worktree. Both are
invisible to `git status` in the main tree — which is exactly how three worktrees (tens to hundreds of megabytes each) went stranded and unnoticed.

Report this before changing state. **Never discard uncommitted work without showing it
first** — a killed worker's edits are indistinguishable from your own until you look.

## 2. `pause` — keep the claims, stop the dispatch (the default)

Use when you want the same tasks resumed later, by you, in the state they are in.

**The claims are already the pause.** A task left `in_progress` does not appear in
`${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh ready`, so nothing will re-dispatch it. You mostly need to stop *new* work starting:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh gate create <epic-id> --reason "paused <date>"
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh update <epic-id> --status blocked      # REQUIRED — the gate alone does NOT park an epic
```

**Both steps.** `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh gate create --blocks <epic>` cannot add the dependency — beads rejects it
with *"epics can only block other epics, not tasks"*. The gate issue is still created (so it
shows in `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh gate list` and carries the audit trail), but only `--status blocked` actually
removes the epic from the queue. Leave the claims, leave the working
tree, leave the branches.

**Then park the working tree honestly** — either commit the partial work on its worker
branch, or leave it uncommitted in the worktree and say so in the report. Both are resume
points; a broken tree is not. If the partial edit does not compile, note that prominently.

**Say where each in-flight task's work is.** For every task that was claimed, run
`${CLAUDE_PLUGIN_ROOT}/harness/swarm/resume-point.sh <id>` and put its line in the report: the branch, the commit count,
whether uncommitted work exists, whether a verdict was recorded. That is the handover.

**To resume:** `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh gate resolve <gate-id>` **and** `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh update <epic-id> --status open` — both, for the same reason — then re-run `/swarm` or `/campaign`. **The
resumed run adopts the work rather than redoing it:** `/swarm` step 5 asks `resume-point.sh`
for every task before dispatching, merges what was already verified, verifies what was only
committed, and re-attaches a worker (`dispatch.sh … --resume <branch>`) to a worktree holding
uncommitted changes. A worker with the same `BEADS_ACTOR` re-claims its own task cleanly —
`--claim` is idempotent for the existing holder.

## 3. `release` — hand the tasks back to the queue

Use when you want someone else, or the next run, to pick this work up fresh.

```bash
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh update <ids> --status open --assignee ""     # verified: returns them to ${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh ready
```

Then, for each released task, **deal with its partial work explicitly**:

- worth keeping → commit it on a branch and note the branch on the task
  (`${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh update <id> --append-notes "partial work on <branch>: <what is done, what is not>"`)
- not worth keeping → `git restore` **only the paths that task touched**, never `git restore .`
  and never `git checkout .`, which would take out a sibling's landed-but-unstaged work

A released task with orphaned edits still in the tree is the worst outcome: the next worker
claims it, finds unexplained changes, and cannot tell whose they are.

## 4. Always — clear the stuck state

```bash
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh slot-check
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh slot-release --holder <name>      # ONLY if a dead worker still holds it
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh autosync on             # pre-flight disabled this and never restores it
git worktree prune                         # drop registrations for worktrees already deleted
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh export           # the jsonl is stale while export.auto was off
git add <the tracked export> && git commit -m "chore(tracker): halt <epic> — <pause|release>"
git push
```

**A merge slot held by a dead worker blocks the next wave forever** — nothing times it out.
Check it every time, and release it only after confirming the holder is genuinely gone.

**`export.auto` is the one that bites silently.** `/swarm` and `/campaign` pre-flight set it
to `false` so tasks cannot stage `issues.jsonl` into a sibling's commit, and no code path turns
it back on. Left off, `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh close` stops keeping the tracked jsonl fresh and your backlog
quietly drifts from your code.

## 5. Report


State exactly: which tasks landed, which are paused (and gated), which were released, what
uncommitted work remains and whose it was, merge-slot state, whether `export.auto` is
restored, and **the one command that resumes** — `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh gate resolve <id>` or the `/campaign`
invocation. Someone returning tomorrow should not have to reconstruct any of it.
