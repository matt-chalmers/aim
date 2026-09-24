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

**`UPGRADE: harness.yaml carries no harness.version`** (or a version behind the plugin's) —
`check-project-config.sh --strict` and `preflight.sh` exit 3. The plugin moved on since the
config was reviewed; the blocks it now reads may not be there. Run `/harness-setup`, which
applies every note in [upgrading](../upgrading.md) since the stamped version and re-stamps.
A patch behind is a warning; a minor behind is a stop.

**`<plugin>:<agent> is a mad-harness agent and runs through the dispatcher, not the Agent tool`**
— the `PreToolUse` hook refused an `Agent(subagent_type=…)` call. Write the prompt to a file
and run the `dispatch.sh` form it printed as a background Bash call; that is where the
tier, the ceiling, the sandbox and the cost record live.

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

**`Skill <x> is not in this session's skills allowlist`** — under `dispatch.lean_catalog`
the worker's Skill catalog is the plugin's skills plus the project's own `.claude/skills/*`.
A skill outside both was never meant to reach a worker; if it is the project's, its
directory is missing a `SKILL.md`. This is not a permission denial, so the dispatch is
still `ok` — `make conformance` asserts the real CLI lists exactly the catalog named.

**A dispatch exited 3** — the budget ceiling killed it. The transcript the run produced and
its spend are recorded (`terminal: budget`, `kills` in `make models-cost`). Raise the tier's
`max_budget_usd` only if the work legitimately needs it; the lever that stops a worker
running into its ceiling is the budget it is *told*, `task_budget_tokens`.

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

## `apply-plan.sh` refused the plan

It validates the whole command block before writing anything and lists every problem: a
label used before its create, a line that is not `tk.sh`, `create --graph`, the positional
`close <id> "msg"`, a label whose create was already applied under a different title. Fix
the plan file and rerun — labels already created are skipped, never re-created. To start an
epic over, delete `.harness/run/apply-plan-<epic>.json`.

## `close-epic.sh` says GATE FAILED — nothing written

One of three gates: a task blocked in prose only (`check-blocking-prose.sh`), an open row
in the decision register, or a staged file surviving its epic. The last is the common one:
regenerate `tasks.md`, fold in, then `archive-epic.sh <epic>` (or delete the folder where
no archive is declared). `--check` runs the gates alone.

## The wave says it merged, but nothing landed

**Check the primary checkout is clean.** `/swarm` disables tracker autosync at pre-flight
and restores it at close-out, so the backend does not stage its export into a sibling's
commit. A wave that skips that ends dirty and the merge is refused.

**Check `.claude/worktrees/` is gitignored.** Otherwise `git add -A` commits worker
worktrees as embedded git repositories.

## A stopped run left worktrees with work in them

Ask before dispatching: `resume-point.sh <task>` says MERGE, VERIFY, REATTACH or FRESH,
and `dispatch.sh --resume <branch>` adopts a REATTACH. `/halt` copies every worktree's diff
and unmerged commits under `.harness/halted-<date>/` before releasing anything, so nothing
is lost to the sweep.

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

## The backlog reads as empty, or a check answers about the wrong project

Symptoms: `tk.sh list` or `tk.sh memories` print nothing with exit 0 against a repository
you know has records; a check reports `project: MAD harness` when you meant yours; a
campaign reports a clean, zero-work run and stops at §1.

The harness has resolved the wrong repository. Every script has to *find* the project it
works on, because as a plugin it lives nowhere near it — and the harness's own directory
carries a `harness.yaml`, so a mis-resolution looks like a valid, empty project rather
than an error. The tracker now refuses that case: `bd` failing to find a workspace is a
`TrackerError`, never an empty list. The first line of every check names the project it
answered about; read it.

```bash
tk.sh backend                    # from inside your repo — should name YOUR backend
MAD_HARNESS_REPO=$PWD tk.sh list # the explicit override, if running from elsewhere
```

Run scripts from inside the consuming repository and nothing needs setting. From
anywhere else — a scheduler, CI, another checkout — set `MAD_HARNESS_REPO` to the
repository root. The full resolution order is in
[scripts → where a script resolves the repository from](../reference/scripts.md#where-a-script-resolves-the-repository-from).

## `tk.sh ready` is empty after switching backend

The backend changed; the records did not move.

```bash
tk.sh migrate --to <new-backend> --dry-run    # confirm what is in the old store
tk.sh migrate --to <new-backend>
```

Migrating assigns new ids, so a commit message or doc quoting an old id still names the old
one. The source store is never modified.

## A tier keeps being killed, or `results%` is high

```bash
make models-cost                          # kills, results%, large, breaks per tier
harness/checks/session-cost.sh <session>  # one session's context curve: what grew it, every jump
```

High `results%` means the agent's own tool results are re-read every turn — whole files
read in one call, `grep -A 400`. The fix is windowed reads (`peek.sh path:START-END`), not a
shorter prompt; `evidence-gathering` says so. `breaks` means the prefix was re-written: a
session left idle over an hour re-writes its whole context at the write rate on the next
request. See [cost](../concepts/cost.md).

## A worker on a third-party provider is cut off almost immediately

Its ceiling is being checked against the wrong price table. Claude Code prices a model it
does not recognise from its own table — measured at ~10x the real cost — so a $3.00 ceiling
bites at a few hundred milli-dollars of actual spend, and it reads as the model failing.

```bash
make models-cost                        # `cost_source` and `ceiling_source` per row
harness/models/probe-compat.sh <name>   # is the provider sound at all?
```

The tier must declare a `price` block; `check-project-config.sh` refuses one that does not,
so a tier that got this far was configured before that check or bypasses it. Add the rates
and the harness meters the dispatch itself. [Providers](../concepts/providers.md).

## A dispatch ended with `unenforceable_ceiling`

Nothing was exceeded — nothing was *checking*. The tier declares a price, so the SDK was
deliberately given no ceiling, and then three turns arrived carrying no usage at all, so the
harness had nothing to meter. It stops rather than run uncapped.

This is a configuration fault, not a task to split or a tier to escalate:

```bash
harness/models/probe-compat.sh <provider>   # the streamed-usage probe reports WARN
```

If that provider genuinely cannot stream usage, bound the tier with `task_budget_tokens`,
which needs no cooperation from it.

## The cost numbers look impossible

Three checks, in order:

1. **Are two pockets being added?** A subscription allowance and an invoiced account are
   both real money and are never summed. `make models-cost` totals them apart; a report that
   shows one number for both is reading the wrong column.
2. **Are two cost sources being compared?** `sdk` is Claude Code's estimate; `priced (...)`
   is computed from declared rates. An A/B arm that mixes them is flagged, not averaged.
3. **Is it a metered kill?** Its cost is prompt tokens only — the output count never
   arrived, because the run was stopped before its final result message.

All three: [providers](../concepts/providers.md) and [cost](../concepts/cost.md).

## The tracker and the docs disagree

```bash
harness/checks/check-blocking-prose.sh      # prose claims a block with no edge
harness/checks/check-decision-register.sh   # register vs. tracker
harness/checks/check-line-pins.sh           # citations that drifted
```
