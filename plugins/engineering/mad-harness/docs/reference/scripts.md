# Scripts

Shell entry points under [`harness/`](../../harness/). Agents invoke these by **absolute
path** — a command spelled with a shell variable can never be permitted, because permission
rules match the text before the shell expands anything.

## Dispatch and tracking

| script | does |
|---|---|
| `models/dispatch.sh <agent> --prompt-file F` | runs one agent through the boundary and records what it cost |
| `tracker/tk.sh <verb>` | the whole tracker surface, backend-independent |
| `tracker/tk.sh migrate --to <backend>` | copy every record into another backend, rewriting ids and edges |
| `tracker/tk.sh lease <acquire\|release\|list\|show\|steal> [epic]` | epic leases, so two machines cannot work one epic |
| `tracker/render-epic.sh <epic>` | the generated, human-readable task view |

`dispatch.sh --dry-run` prints the resolved model, tier, budget, permission mode and grants
without spending anything. It is the fastest way to see what a dispatch *would* do.

## Verification

| script | does |
|---|---|
| `verify/brief.sh <task> [sha]` | builds the shared brief. Prints its path on **stdout**, a size note on **stderr** |
| `verify/run.sh <key> [--scoped PATH]` | runs a stack's declared commands, loading the worker environment for you |
| `verify/peek.sh` | reads many files or slices in one tool call |
| `verify/scan.sh` | answers many search questions in one tool call |
| `verify/mutate.sh` | a mutation batch in a tree that cannot carry stale bytecode |
| `verify/fidelity-check.sh` | one screen against its design handover |

`peek` and `scan` exist because a lens that opens twenty files one at a time spends its
budget on tool overhead rather than judgement.

**Never `source .swarm-env`.** Run through `verify/run.sh`, which loads it. Sourcing cannot
work regardless: every Bash call is a fresh shell, so the exports would not reach the next
command — and `source` evaluates its argument as shell code, so no permission rule matches
it.

## Worktrees

| script | does |
|---|---|
| `swarm/swarm-worktree-init.sh <n> <lane>` | prepares one worker's isolated checkout |
| `swarm/worktree-sweep.sh` | reclaims worktrees a wave left behind |

The sweep detects the default branch rather than assuming `main`. A sweep that dies leaves
every worktree behind, which is the state it exists to prevent.

## Telemetry

| script | does |
|---|---|
| `campaign/campaign-telemetry.sh` | the campaign-level series |
| `make models-cost` | reads the dispatch cost series back, per agent and tier |

## Checks

Sixteen, listed in [checks.md](checks.md).

## Where a script resolves the repository from

The difference matters, and it is deliberate.

**From their own location** (`dirname "$0"/../..`) — `check-record-size.sh`,
`check-blocking-prose.sh`, `check-decision-register.sh`, `spec-index-status.sh`,
`campaign-telemetry.sh`, `swarm-worktree-init.sh`, and both fidelity `.mjs`. These run from
**any** working directory, including outside the repo.

**From the current directory** (`git rev-parse --show-toplevel`) — `check-analyst-mirror.sh`,
`check-doc-drift.sh`, `check-line-pins.sh`, `worktree-sweep.sh`, `mutate.sh`. These must be
run from **inside a checkout**, and they act on *that* tree. That is deliberate for the
worktree-aware ones: running them from `/tmp` fails with `fatal: not a git repository`
rather than silently acting on the wrong tree.
