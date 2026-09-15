---
name: worker-protocol
description: The contract every swarm worker runs under — the isolated worktree and its per-worker database, the commit and merge-slot protocol, the resource bans, and the ten-line return contract. Preloaded by fullstack-engineer and quality-engineer, the two agents that write code inside a worktree.
---

# Worker protocol

You are one worker in a wave. Several siblings are editing the same repository at
the same time. Everything here exists because one of them once trod on another.

<!-- HARNESS CONVENTIONS: mirrored in evidence-gathering, worker-protocol and
     spec-lifecycle, and verified byte-identical by check-conventions-mirror.sh. Edit
     one and the check fails; edit all three or none. -->

## Harness conventions

These are the harness's own rules about the artefacts the harness owns — task text, lens
reports and return lines. They are stated here rather than cited from a project file
because the harness defines them and its own checks enforce them.

**Pair every task id with a short gloss.** `PROJ-4f2a` tells a reader nothing; they
have to look it up to follow the sentence. Write `PROJ-4f2a (retry budget on the ingest job)` —
six words maximum, ideally three or four. It is a handle, not a summary. Use the same
gloss for the same task all session, so a reader can track it across a wave. The bare id
is correct in commit messages and in the tracker’s own arguments, where it is the identifier.

**Cite code by symbol, never by line number**, in anything persisted to a task. Write
`services/billing.py::recompute_invoice_total`, not a line. A symbol survives edits above
it; a line number survives none of them, and a stale pin that lands on plausible-looking
wrong text is worse than one that obviously misses. For prose, quote the opening words
instead — quoted text is greppable. Line numbers are fine in a lens report or in chat,
which is what `peek.sh` emits them for; the ban is on what gets written down.

<!-- END HARNESS CONVENTIONS -->

## Where the harness scripts are

`${CLAUDE_PLUGIN_ROOT}/harness/...`, as written throughout this file. The plugin loader
substitutes the real install path before you ever see the text, so what reaches you is
already absolute and needs no resolving.

**Write it that way and nothing else.** Three spellings that look equivalent are not:

| what you write | what happens |
|---|---|
| `${CLAUDE_PLUGIN_ROOT}/harness/…` | expanded at load; permitted; runs |
| `$HARNESS_ROOT/…` | a SHELL variable. The dispatcher sets it, an interactive session does not — and no permission rule can match a command naming a variable, because matching is textual |
| `harness/…` | resolves only when the harness happens to sit inside the repository you are working on, which it usually does not |

Measured, in frontmatter and on the command line alike. The middle row cost this harness
every interactive invocation of every command until it was found.

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
