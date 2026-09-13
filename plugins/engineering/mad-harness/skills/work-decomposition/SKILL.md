---
name: work-decomposition
description: How to slice a goal or epic into a reviewable task DAG that a parallel wave can actually execute — the one-task rule, dependency edges over hope, the file-contention matrix, and the split-brain check. Preloaded by planner (which produces the DAG) and architect (which designs the shape it will take).
---

# Work decomposition

You **propose**; the main thread **executes**. Never run a a tracker write command
yourself — the split is the guarantee.

## The slicing rule

**One task = one commit = one reviewable change = one acceptance criterion the
verifier can check.**

If a task needs two commits to be reviewable, it is two tasks. If its acceptance
criteria cannot be checked without running two unrelated suites, it is two tasks.
Changed lines per acceptance criterion above ~400 means the slicing rule slipped.

**Never cut a test task as the sibling of an implementation task.** Tests are part
of the work, not a follow-up; a test task that can be dropped is a change that
shipped untested.

## Ordering is edges, not hope

A task that must follow another gets a **dependency edge**. `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh ready` reads edges,
not prose — a note saying "GATED ON X" gates nothing, and the task will be handed
to a worker who then hits the unanswerable question the note warned about. Three
such tasks were found in one session, each stated plainly in a note and each
dispatchable anyway.

```bash
${CLAUDE_PLUGIN_ROOT}/harness/checks/check-blocking-prose.sh    # finds tasks blocked only in prose
```

## The file-contention matrix is your most important output

Two tasks in the same wave that touch the same file will conflict, and a merge
conflict is a planning miss by definition — not something for the workers to
resolve. Produce the matrix, and drop the later task to the next wave.

**A file past `harness.yaml` → `signals.megafile_lines` is an automatic contention edge**, whatever the tasks
intend to change in it.

## The split-brain check

**No two tasks in the same wave may be able to answer the same question
differently.** If two tasks could each decide the same open question — a field
name, a status value, an error shape — and are built in parallel, they will decide
it differently and both will pass their own tests. Serialise them, or settle the
question first.

## One required line per task: the invariant surface

For every task, name the decision records, owner decisions and declared `security.invariants` its
change touches — a `SURFACE:` line. **Build it from the epic's `SPEC INDEX`, not
from a fresh sweep**, and open what the index points at before assigning a task to
it: the index says where to look, not what it says.

This exists because a task that stays silent about its surface gets built silently
against it. **The verifier lenses read this line** — it is what lets
`verifier-security` fire on a task whose path grep is zero.

## Revising an existing plan

You may add, delete or modify tasks to make a plan well constructed. Output a
revision plan giving each task exactly one of `keep` / `merge into <id>` /
`split` / `delete` / `re-edge`, with the reason. Destructive edits you **propose**;
the main thread executes them.

## What this does not cover

Task ids, glosses and the ban on line pins are harness conventions — see the block in
`evidence-gathering`, `worker-protocol` or `spec-lifecycle`. Wave execution,
lanes and the merge slot are `/swarm` and `worker-protocol`. The epic loop is
`campaign-loop`.
