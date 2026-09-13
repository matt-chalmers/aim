# Troubleshooting

Symptoms and their actual causes. Every entry here is a failure this harness has really
had, not a hypothetical.

## Dispatch refuses to start

**`this harness dispatches only on machines where the sandbox can be enforced`**

Working as designed — see [requirements](../requirements.md). Install `bubblewrap` on
Linux, or run inside WSL2 on Windows. The refusal is deliberate: falling back to permission
rules alone is a posture this project does not ship, and a machine quietly running without
containment is the failure the check prevents.

**`provider <x> needs <VAR> in the environment and it is unset`**

An empty credential fails later as a 401 that reads like a provider outage, so dispatch
refuses up front.

**`<agent> declares isolation: worktree but would run in the primary checkout`**

Pass `--worker N`, which prepares the worktree. A wave that proceeds anyway corrupts the
main tree in a way no test catches.

## A worker reports BLOCKED or PASS without doing the work

**Check the denial count first.** It is printed to **stderr**, and recorded in telemetry as
`denied_tools`. A dispatch is marked not-ok on any denial, because headless mode does not
block on a missing permission — it denies the tool, lets the model carry on, and returns
success. A worker denied its test command can still report PASS.

```bash
grep -A6 'permission denial' <the wave's err-*.txt>
```

**If the command was refused with no remedy**, a `Permission:` record has been filed. Answer
it with `/decision`.

## Every command in a worker fails with *Operation not permitted*

The toolchain cannot reach its cache. Declare `cache_env` in the stack module so the cache
lives inside the sandbox boundary — see [stacks](../reference/stacks.md). Granting the
home-directory path does **not** work; it fails `EPERM` on a file inside the granted
directory.

## A command is denied that looks perfectly safe

Three shapes can never be granted, because permission rules match the command **text**
before the shell expands anything:

| shape | why | instead |
|---|---|---|
| `a && b`, `a; b`, `a \| b` | a compound matches no rule even when every part is granted | issue the parts as separate calls |
| `VAR=x cmd` | the string starts with `VAR=`, not the command | let the runner supply the environment |
| `$DIR/script` | matching is textual; the variable is not expanded first | use the absolute path |

## The wave says it merged, but nothing landed

**Check the primary checkout is clean.** `/swarm` disables tracker autosync at pre-flight
and restores it at close-out, so the backend does not stage its export into a sibling's
commit. A wave that skips that ends dirty and the merge is refused.

**Check `.claude/worktrees/` is gitignored.** Otherwise `git add -A` commits worker
worktrees as embedded git repositories.

## Worktrees pile up

```bash
harness/swarm/worktree-sweep.sh
```

It detects your default branch rather than assuming `main`.

## A check fails that you believe is right

**Read what it matched.** Three times here a corpus sweep flagged its own documentation —
the text explaining a banned pattern necessarily contains it. Scope the sweep to shipped
code and to invocations, not to prose.

## Lens verdicts differ between runs

Usually the acceptance criterion is ambiguous rather than the lens unreliable. A criterion
two careful readers can disagree about is a spec defect; give it a worked example.
`analyst-survey` exists to catch that upstream.

## Two machines are fighting over the same work

Symptoms: the same task claimed twice, merges from an unexpected host, or the tracked
export conflicting on every push.

```bash
tk.sh lease list        # which epics are held, and by whom
tk.sh lease show <epic> # holder, host, pid, and how long ago
```

Claims and the merge slot are local to a checkout and coordinate nothing between machines.
Take an epic lease before starting one. If a machine crashed holding a lease,
`tk.sh lease steal <epic>` reclaims it past its TTL and refuses while it is still fresh —
the steal is recorded on the remote rather than applied silently.

Disjoint epics still share one tracked export, so conflicts there become rare rather than
impossible.

## `tk.sh ready` is empty after switching backend

The backend changed; the records did not move.

```bash
tk.sh migrate --to <new-backend> --dry-run    # confirm what is in the old store
tk.sh migrate --to <new-backend>
```

Migrating assigns new ids, so a commit message or doc quoting an old id still names the old
one. The source store is never modified.

## The tracker and the docs disagree

```bash
harness/checks/check-blocking-prose.sh      # prose claims a block with no edge
harness/checks/check-decision-register.sh   # register vs. tracker
harness/checks/check-line-pins.sh           # citations that drifted
```
