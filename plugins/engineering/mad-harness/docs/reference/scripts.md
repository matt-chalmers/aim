# Scripts

Shell entry points under [`harness/`](../../harness/). Agents invoke these by **absolute
path** — a command spelled with a shell variable can never be permitted, because permission
rules match the text before the shell expands anything.

## Dispatch and tracking

| script | does |
|---|---|
| `models/dispatch.sh <agent> --prompt-file F` | runs one agent through the boundary and records what it cost; `--digest [N]` prints the first N lines and the path of the file holding the whole result (`.harness/run/out/`), `--out <path>` names it |
| `tracker/tk.sh <verb>` | the whole tracker surface, backend-independent |
| `tracker/tk.sh note <id> --file <path>` / `update <id> --append-notes-file <path>` | a note from a file — a dispatch's `--digest` output attached to the epic without the orchestrator reading it in and emitting it again |
| `tracker/tk.sh migrate --to <backend>` | copy every record into another backend, rewriting ids and edges |
| `tracker/tk.sh lease <acquire\|release\|list\|show\|steal> [epic]` | epic leases, so two machines cannot work one epic |
| `tracker/tk.sh park <epic> --reason …` / `unpark <epic> [--gate]` | park an epic — the gate AND `status: blocked`, one verb — and its mirror. A gate alone never removed an epic from the queue; this was a two-step rule stated in four places and drifted in one |
| `tracker/tk.sh ready --label <lane>` | the ready set filtered to one lane's label |
| `tracker/render-epic.sh <epic>` | the generated, human-readable task view |

`dispatch.sh --dry-run` prints the resolved model, tier, budget, permission mode and grants
without spending anything. It is the fastest way to see what a dispatch *would* do.

## Verification

| script | does |
|---|---|
| `verify/brief.sh <task> [sha]` | builds the shared brief. Prints its path on **stdout**, a size note on **stderr** |
| `verify/run.sh <key> [--scoped PATH]` | runs a stack's declared commands, loading the worker environment for you; keeps each whole log at `.harness/run/out/` and reports a failure digest plus the path |
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
| `swarm/worktree-sweep.sh [--apply] [--prune-orphans]` | reclaims worktrees a wave left behind, and classifies worker refs that no worktree points at |
| `swarm/preserve-worktrees.sh [--to DIR]` | writes every worker worktree's uncommitted diff, untracked files and unmerged commits to a dated folder — `/halt`'s first step, before anything is removed |
| `swarm/resume-point.sh <task-id> [--json]` | where a task's work already is — MERGE, VERIFY, REATTACH or FRESH — so a resumed run adopts it instead of redoing it |
| `swarm/pinned.sh [--always] [--transcript PATH]` | the campaign's pinned state — claims, merge slot, worktrees, the loop's rules — read from disk; the `SessionStart` hook prints it after a compaction, resume or start whenever a campaign is in flight, naming every pinned id the compaction summary dropped; at every session start, resume and compaction it also prints the orchestrator card |
| `swarm/guard-agent-tool.sh` | the `PreToolUse` hook: refuses the Agent tool for this plugin's agents and prints the `dispatch.sh` form to use — the tier, ceiling, sandbox and cost record live only there |
| `swarm/preflight.sh` | campaign-loop §0 and /swarm step 1 as one call: clean tree, config current (exit 3 = upgrade), merge slot free, autosync off, ports unbound, disk, worktree prune, the sweep (IN FLIGHT refs stop the run), its --apply, stack-command repair, record sizes — was six calls at the orchestrator's context price, then four more |
| `swarm/apply-plan.sh <plan.md> --epic <id> [--dry-run] [--render <path>]` | applies a planner's labelled command block to the tracker: validates the whole plan first, resolves `T1:` labels to real ids, records the map under `.harness/run/` so a rerun skips what exists, ends with `validate` and the render |
| `swarm/campaign.sh [--max-epics N] [--epic ID]` | `/campaign-auto` as one fresh headless orchestrator session per epic: pre-flight once, then `dispatch.sh campaign-orchestrator` per open epic, reading its digest; exit 5 when the usage window closes |
| `swarm/close-epic.sh <epic> --reason "…" [--check] [--no-push]` | campaign-loop §5 as one call: blocking-prose, decision register and staging/archive checks (nothing written if any fails) → close → export → tracker commit → pull --rebase + push → autosync on |
| `swarm/close-wave.sh <id>[=<reason>]… [--reason] [--message] [--epic] [--no-push] [--restore-autosync] [--check] [--sync-only]` | /swarm step 9 and /grind §10 as one call: each close ascending by id → export → every affected epic's view (drift said before it is regenerated) → add + commit → pull --rebase --autostash (an upstream that moved stops before the push) → push → status → autosync on only with `--restore-autosync` (a standalone run, never a wave inside a campaign). `/halt` uses `--sync-only` for its tail |

The sweep detects the default branch rather than assuming `main`. A sweep that dies leaves
every worktree behind, which is the state it exists to prevent.

`resume-point.sh` is asked before every writer dispatch (`/swarm` step 5). It finds the
worker branches whose commits name the task — the harness's own `harness-w*` and Claude
Code's `worktree-agent-*` — reads the `VERIFIED <sha>` note the lens step records, and
checks the branch's worktree for uncommitted work. `dispatch.sh --resume <branch>` then
attaches a worker to that branch rather than cutting a new one from HEAD.

## Telemetry

| script | does |
|---|---|
| `campaign/campaign-telemetry.sh` | the campaign-level series |
| `make models-cost` / `checks/models-cost.sh` | reads the dispatch cost series back, per agent and tier |
| `checks/session-cost.sh <transcript.jsonl | session-id> [--json] [--top N]` | one session's context curve in tokens: requests, first/last/average context, what grew it (outputs, injected text, tool results), every jump over 15k and what landed it — the orchestrator-side measurement |

## Checks

Listed in [checks.md](checks.md), generated from the directory.

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

**Two things are resolved, not one.** `REPO` is the *project* — its `harness.yaml`, its
tracker, its primary checkout — and is what the rules above find. `CHECKOUT` is the git
working tree the caller *stands in*, which for a dispatched worker is its own worktree.
Everything that runs or reads code — `verify/run.sh`, `scan.sh`, `peek.sh`, `brief.sh`, the
worker's `.swarm-env` — uses `CHECKOUT`; the tracker and config use `REPO`. They were one
variable, and a worker's `run.sh` tested the primary checkout — green with the worker's
own code broken — until a worker caught it. The dispatcher hands a worker `MAD_HARNESS_REPO`
and deliberately *not* its own `MAD_HARNESS_CALLER_PWD`, so each wrapper the worker calls
records the worktree it was called from.

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
