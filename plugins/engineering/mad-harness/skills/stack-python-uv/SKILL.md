---
name: stack-python-uv
description: "Running things in a uv-managed Python repository: scoped test invocation, per-worker database isolation, and the command form that has stalled this harness for hours. Loaded ON DEMAND — never preloaded."
---

# Python + uv: running things

**Loaded on demand.** Your prompt's technology card carries the rules that matter
most; this is the depth. The exact commands come from the stack module — run
`${CLAUDE_PLUGIN_ROOT}/harness/checks/check-project-config.sh` rather than assuming them.

## Scope every run

Run the tests for what you changed, not the whole repository. A whole-repo run in a
worker costs minutes it does not have and tells you nothing your scoped run did not.

## The command form that hangs

**Never prefix a command with an inline environment assignment:**

```
VAR=value uv run pytest        # WRONG — hangs indefinitely
${CLAUDE_PLUGIN_ROOT}/harness/verify/run.sh --lane <lane> test_scoped <path>   # correct
```

The first form's command string starts with `VAR=`, not `uv`, so no `Bash(uv:*)`
prefix permission rule can match it. The call stops on a permission prompt that
surfaces in the *orchestrator's* session — invisible to you, and indefinite when
nobody is watching. This has cost this harness eight and a half hours, three times,
and the first two fixes were documentation that the scripts then contradicted.

**`source .swarm-env && uv run pytest` is the same trap and was once the fix for it.**
Measured: a compound command is denied even when every part is granted — `Bash(source:*)`
plus `Bash(uv:*)` still refuses it, while a plain `uv run pytest` is allowed. `run.sh`
loads `.swarm-env` itself, so the isolated database arrives without a command string that
no rule can match.

## Per-worker isolation

Run through the harness runner. It loads `.swarm-env` for you, which carries this
worker's own resources — its database above all. Without them a worker falls through to
the shared default, and parallel workers overwrite each other's fixtures **while every
suite still passes**. That
silence is the whole problem: the corruption surfaces days later as an unrelated
flake in someone else's wave.

A first run against a fresh database needs the create-database flag; subsequent runs
reuse the schema.

## Dependencies

`uv sync` hardlinks from a global cache, so restoring a fresh worktree costs seconds.
That is why each worktree gets its own environment rather than sharing one — the
isolation is nearly free.
