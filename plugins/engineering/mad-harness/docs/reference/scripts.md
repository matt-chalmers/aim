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

Twenty, listed in [checks.md](checks.md).

## Where a script resolves the repository from

Installed as a plugin, the harness lives in `~/.claude/plugins/cache/…`, nowhere near
the repository it works on — so every script has to *find* that repository, and finding
the wrong one is not an error. It is a clean pass about somebody else's project, or a
tracker returning an empty backlog with exit 0.

**Every wrapper records the caller's directory before it moves.** Each one does
`cd "$(dirname "$0")/.."` to reach the harness, which carries its own `harness.yaml`
describing the harness as a project; a walk-up from *that* directory finds it and stops.
So the wrappers export `MAD_HARNESS_CALLER_PWD` first, and the resolver looks from there.
Resolution order, in [`harness/models/resolve.py`](../../harness/models/resolve.py):

1. **`MAD_HARNESS_REPO`**, if set — an explicit override, for tools run from outside a
   checkout, for CI, and for the test suite (which sets it to the plugin itself).
2. **Walk up from the caller's directory** for a `harness.yaml`. A project is defined by
   its config, not by where `git init` happened, so a plugin nested inside a monorepo
   still resolves to the plugin.
3. **`git rev-parse --show-toplevel`, from the caller's directory** — for a repository
   that has no `harness.yaml` yet, which is where `/harness-setup` writes one.
4. Otherwise it **fails, naming the directory it tried.** The harness's own tree is never
   the answer for a caller outside it.

In practice: run any script from inside the consuming repository and nothing needs
setting. From anywhere else, set `MAD_HARNESS_REPO=/path/to/repo`. Every check prints
`project: <name>` so a wrong resolution is visible in the first line.

**The exceptions resolve from their own location** — `check-analyst-mirror.sh`,
`check-conventions-mirror.sh`, `check-script-refs.sh`, `check-skills.sh`,
`check-model-config.sh`. They validate files that ship *with the plugin* (agents,
skills, the tier table), which a consuming repository has no copy of.

**Worktree-aware scripts act on the checkout they run in** — `worktree-sweep.sh`,
`mutate.sh`. Running them from `/tmp` fails with `fatal: not a git repository` rather
than acting on the wrong tree, which is deliberate.
