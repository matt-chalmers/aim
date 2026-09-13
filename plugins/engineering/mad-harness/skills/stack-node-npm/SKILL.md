---
name: stack-node-npm
description: "Running things in an npm-managed Node repository: scoped tests, the shared dependency directory, and why parallel workers must not share build output. Loaded ON DEMAND — never preloaded."
---

# Node + npm: running things

**Loaded on demand.** Your prompt's technology card carries the rules that matter
most. Exact commands come from the stack module, not from memory.

## Scope every run

Run the tests for the paths you touched. Whole-repo lint, typecheck and test belong
to the orchestrator, not to a worker.

## Dependencies are shared, build output is not

`node_modules` is hundreds of megabytes and concurrent readers are safe, so a worker
worktree **symlinks** it to the primary checkout rather than reinstalling. This is the
opposite of the right answer for a Python virtualenv, and the difference is not
arbitrary: installing is cheap there and expensive here.

Build output is the reverse — parallel builds must never share a directory. Each
worker exports its own, and that variable is what keeps two workers from corrupting
one another's build.

## Dev servers

Already running. Never start another; never run a production build while one is live.
The two share a build directory and the production build corrupts it for both,
costing a delete-and-restart to recover.
