---
name: worker-protocol
description: The contract every swarm worker runs under — the isolated worktree and its per-worker database, the commit and merge-slot protocol, the resource bans, and the ten-line return contract. Declared by fullstack-engineer and quality-engineer, the two agents that write code inside a worktree.
---

# Worker protocol

You are one worker in a wave. Several siblings are editing the same repository at
the same time. Everything here exists because one of them once trod on another.

The harness conventions — task glosses, cite-by-symbol — and where the harness scripts
are (`${CLAUDE_PLUGIN_ROOT}/harness/...`, written exactly so) reach you through
`evidence-gathering`, which you preload alongside this. Measured: a worker that preloads
both paid for two copies of each on every dispatch.

## Your worktree is prepared for you — do not prepare it yourself

The dispatcher runs `${CLAUDE_PLUGIN_ROOT}/harness/models/dispatch.sh --worker <n>`, which creates the
worktree **and** runs `${CLAUDE_PLUGIN_ROOT}/harness/swarm/swarm-worktree-init.sh` inside it before you
start. You open in a tree whose dependency directories are already restored and
which carries its own `.swarm-env`. Do not re-run the init script.

This is load-bearing rather than convenient: **`claude -p` does not honour
`isolation: worktree` from agent frontmatter** — measured. Dispatched headless
without `--worker`, a writer would run in the *primary checkout* alongside every
sibling. The dispatcher refuses that rather than allowing it, so if you find
yourself in the primary checkout, something is wrong — stop and say so.

## Run your tests through `run.sh`, which carries your isolated environment

```bash
${CLAUDE_PLUGIN_ROOT}/harness/verify/run.sh --lane <lane> test_scoped <path>
```

**Not `source .swarm-env && <runner> …`, which cannot be permitted.** A compound command
is denied even when every part of it is granted separately — measured, and the plain
command is allowed in the same breath. In a dispatch nobody is watching that denial is
silent, and the worker either stalls or reports work it never did. Your stack's own skill
carries the measurement.

That is the same family as the inline-assignment trap below, and `source && …` was once
the FIX for it. It carried the same defect.

`run.sh` loads `.swarm-env` for you and runs the command where it belongs. Your
`.swarm-env` exports **your own** per-worker resources — above all your own database.
Without them your suite falls through to the shared default and parallel workers silently
corrupt each other's fixtures. The suite still goes green, so nothing announces it; it
surfaces later as an unrelated flake in somebody else's wave.

**Never prefix a command with an inline environment assignment** —
`VAR=value <runner> ...`. That command string begins with `VAR=` rather than the
runner, so no prefix-based permission rule can match it, and the call stops on a
permission prompt that surfaces in the *orchestrator's* session. In an unattended
run nobody is there. That is a recorded 8.5-hour stall, and it has come back three
times — the harness now sweeps every tracked file for it.

**Do not go hunting for the command.** `${CLAUDE_PLUGIN_ROOT}/harness/verify/run.sh --lane <lane> <key>`
resolves it from your stack's config and runs it where it belongs — including
the first-run flag a fresh database needs. Your card carries the handful of
rules that are wrong most often; the commands come from `run.sh`.

## If your prompt begins `RESUMING` — you are continuing, not starting

A previous run of this task was stopped after work began. Your worktree is attached to the
branch that holds it, and the preamble tells you how many commits are there and whether
uncommitted changes sit on top. **Before anything else:** `git log --oneline main..HEAD` and
`git status`, then read what exists. Continue from it. If part of it is wrong, fix it in
place — do not recreate files that are already there, do not start a parallel
implementation, and do not reset the branch. Your commit(s) go on this branch, through the
merge slot as below. If the existing work contradicts the task as written, say so in your
return rather than silently choosing.

## One task, one commit, through the merge slot

- **One task is one clean commit.** Not two, not a commit plus a fixup. A lens
  judging your work reads the diff of a single commit.
- **Take the merge slot before committing** (`SWARM_SLOT`). The commit phase is
  serialized precisely so two workers cannot land at once; a wave with a merge
  conflict is a step-3 planning miss by definition, not something to resolve by
  force.
- **Conventional commit message**, referencing the task id. Bare ids are correct
  in commit messages — the gloss rule applies to prose, not to git.

## Resource bans

Your siblings are using the machine too. Do not start the dev servers, do not bind
any port they use, do not run the full end-to-end stack, and do not run container
tooling against shared services. The end-to-end lane owns its ports exclusively and
is dispatched as a serial lane of one — never from inside a parallel wave.

## The return contract — ten lines maximum

```
<task id> · PASS|FAIL|BLOCKED|SKIPPED|NEEDS-SERIAL-LANE · CORE-CHANGE <path> if used
files touched · tests run and result · commit sha · docs updated · one risk note
```

Anything longer goes on the task with `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh update <id> --append-notes`, not into
the orchestrator's context. Before you claim PASS: did you *watch* the tests pass?
Are they adversarial against real data? Did you update every doc this touched? Is
it exactly one task in one clean commit?

Refer to tasks as `<id> (<short gloss>)` in your return text; a bare id tells the
reader nothing without a lookup.

## What this does not cover

What "tested" means is `test-doctrine`. How to search and read cheaply is
`evidence-gathering`. The core-change licence is in your own agent definition.
