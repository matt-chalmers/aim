---
description: Run one wave of parallel workers over the ready queue for a lane, with a serialized commit phase, a whole-repo wave gate, and a push at wave end
argument-hint: "[lane — one declared in harness.yaml] [n: 1-4]"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/harness/*), Task, Bash(git:*), Bash(make:*), Read, Glob, Grep, AskUserQuestion
---

<!-- ORCHESTRATOR CARD: mirrored from harness/orchestrator-card.md by
     check-orchestrator-card.sh --write; edit it there, never here. -->
**You are the most expensive caller in the system.** Measured: an orchestrator's context averaged ~210k tokens in its campaign, ~380k over its session; each tool call re-reads it, three to six times a worker's price. Four rules:

1. **Never load reference material into yourself.** A built-in agent (`Agent(subagent_type="general-purpose")`) loads it and answers; its whole return lands in your context, so ask for a few lines or a path. Measured: one reference skill loaded here cost $11.21 over 64 turns; ~$2 in a subagent.
2. **One call where five would do.** Every `swarm/*.sh` is a whole sequence (preflight, apply-plan, close-wave, close-epic); `scan.sh`, `peek.sh`, `run.sh` batch; `tk.sh` once, `--json`.
3. **Artefacts by path.** `dispatch.sh … --digest`, `tk.sh note --file`: a plugin agent's result goes from its file to what consumes it, never through you.
4. **An hour idle, and the next request re-writes your whole context at the write rate.** Measured: four gaps re-wrote 3.5M tokens of one session, more than its campaign cost. Back at a large session, weigh its context against that, or start fresh.

Plugin agents run through `dispatch.sh`; a hook refuses them the Agent tool.
<!-- END ORCHESTRATOR CARD -->

Run one swarm wave: **$ARGUMENTS** (default lane `backend`, default n `2`).

You are the orchestrator. You own every shared resource; workers own none of them.

## Lane caps — clamp `n` to these

**The caps live in `harness.yaml` → `lanes`**, because a cap is a measured property of your
machine and stack: how much memory one test process holds, whether an expensive build lives in
this lane, which lane owns an exclusive port. Read them there; each lane names its `agent`, its
`cap`, and the `constraint` that set it.

**Cap by agent class as well — `lanes` caps writers by lane and says nothing about
lenses.** Nine lens dispatches against a global cap of 4 was three serial batches, the last
using a quarter of the machine. The global cap is whatever `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` is set to in your
environment; the per-class table below caps *within* it:

| Class | Agents | Cap | Constraint |
|---|---|---|---|
| Read-only, no execution | `verifier`, `verifier-spec`, `architect`, `planner`, `analyst` | **8** | I/O-bound, ~0 process RSS |
| Read-only, executes | `verifier-tests` | **2** | test processes; **derive its isolated resources from the task id**, never a fixed name per lens |
| Writers | `fullstack-engineer`, `quality-engineer` | lane cap | dev servers, builds, per-worker resources |
| Browser-driving | `fidelity-auditor` | **2** | ~400–700 MB browser each |

**The global cap is the real ceiling; the lane caps only decide the MIX.** Raising a lane above
it would be theatre. **`fidelity-auditor` stays at 2** — the one cap that has never moved *up*.
Three concurrent browsers is where a wave dies to the OOM killer; the same three audits still run, in two
batches. Concurrency is not an input to any lens's verdict, which is exactly what keeps them
decorrelated.

## 1. Pre-flight

```bash
${CLAUDE_PLUGIN_ROOT}/harness/swarm/preflight.sh          # ONE call — eleven steps, in the loop's order; exit 0 ready, 3 config upgrade, 1 otherwise
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh memories      # the field-guide index for this wave's subject matter — content, not a gate
```

| `preflight.sh` step | what it is | on failure |
|---|---|---|
| `git status --porcelain` | the tree is clean — another session mid-edit here loses work | stop |
| `check-project-config.sh --strict` | the config was reviewed against this plugin (exit 3 = it was not: run `/harness-setup`) | stop, both modes |
| `tk.sh slot-check` | the merge slot is free, or its holder is provably gone | stop |
| `tk.sh autosync off` | **the one tracker write** — the backend must not stage its export into a worker's commit | skipped after any failure above |
| `check-ports.sh`, `df -h .` | declared ports unbound; disk headroom (advisory) | on the line |
| `git worktree prune`, `worktree-sweep.sh`, `--apply` | forget worktrees whose directory is gone; **the real sweep**, classify-don't-delete — merged and clean worktrees reclaimed, uncommitted work never touched; then a second pass over the refs | **IN FLIGHT > 0 stops the run**: committed work for an open task that no worktree holds. Adopt each ref (`resume-point.sh <id>`: merge it, or dispatch the task *from* it) before that task is dispatched again |
| `check-stack-commands.sh --repair` | do the declared commands still work; a broken one is repaired from the repo | a repair **changed `harness.yaml`** — commit it with the wave |
| `check-record-size.sh` | records approaching the ~64KB ceiling past which `tk.sh note` hard-fails (advisory) | on the line |

It was six calls at your context's price, then four more the loop ran after them; the
sweep's IN FLIGHT count was read from its text and the `--apply` skipped whenever the dry
run "looked fine" (a campaign reached **35** stranded worktrees, and a pre-flight once found
**70** orphaned refs, 56 unmerged, for tasks then re-dispatched from scratch). The
classification tables, the 2026-08-27 incidents that shaped them — a worktree holding a
**staged revert** of its own fix, another holding the **only** copy of an uncommitted change
— and why `git worktree prune` alone is a no-op live in one place:
`worktree-sweep.sh`'s own header. **Merge from the branch ref, never from a
worktree you did not just create.**

**Do not run `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh prime` here or give it to a worker.** Its session-close protocol says
`[ ] 4. git push … Work is not done until pushed`, and a worker must never push — it commits
inside the merge slot and you own integration. `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh memories` is the index;
`${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh recall <key>` pulls a body.

## 2–3. Compose the wave, and the contention re-check — one call

```bash
${CLAUDE_PLUGIN_ROOT}/harness/swarm/wave-plan.sh <lane> [n] [--parent <epic>]      # opens the epic's wave manifest with --parent
```

| what it does | rule |
|---|---|
| `tk.sh ready` for the lane | tasks carrying the lane's label; none labelled → every ready task, and it says so |
| top `n` by priority, oldest first among equals | clamped to `min(n, lanes.<lane>.cap, CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS)` — **the global cap is the real ceiling; the lane cap decides the MIX** |
| `resume-point.sh` for every candidate | **MERGE** needs no worker and is listed for step 8; VERIFY and REATTACH carry their branch into step 5 |
| the paths each task names, that exist | **two candidates sharing a path → the lower-priority one is dropped, and it says which path.** The planner should have caught it; this is the backstop |
| any shared path past `signals.megafile_lines` | **a lane of width 1 for this wave, however disjoint the functions look** — the check the dependency graph physically cannot do: `tk.sh validate` rated an epic 11-wide when five of its six wave-1 tasks touched one very large shared file |
| `git merge-tree` between candidates that already have branches | a pair that conflicts drops the lower; git unable to say is *said*, never read as clean |

Then **two judgements on what it prints, and they are yours**:

**Shared vocabulary.** Read the candidate titles and first lines side by side and name any
**shared new thing** — the same new field, error code, decision-record number, component or
schema. If two tasks would each *introduce* it, one must **consume** it instead: drop the
second and say so. Same failure class as a shared path, different surface.

**New files — the one the path sweep structurally cannot do.** Every mechanical check above
operates on files that **already exist**. Two tasks that each *create* the same new file
collide **invisibly**. The script lists what each candidate says it will create; compare
the answers. The usual suspects, in order of how often they collide:

| Likely new file | Why it collides |
|---|---|
| a **test-data factory module** for a context | the most likely collision — every lane wants one |
| a shared test-fixture file at a new level | same |
| a new module in the shared services layer | two tasks both "needing a helper" |
| a conventional per-context file that is currently empty | an empty file is not an existing file |
| a new test module for one feature | two tasks slicing the same feature |

This is not theoretical: two tasks in one wave each created the same new test-factory module,
the contention check passed clean, and the conflict surfaced at merge. **If two tasks in a wave
touch the same context's tests at all, treat a shared factory module as presumed contention and
name which task owns it.**

## 4. Confirm

`AskUserQuestion` with the task ids, titles, the agent each maps to, the files each will
touch, and the `SWARM_DB` assigned.

## 5. Dispatch — through the harness boundary, all `n` in a SINGLE message

**Before dispatching any writer, ask where its task's work already is.** A stoppage after a
worker started — the environment killed mid-wave, a lens still running, a branch committed but
not yet merged — leaves a branch, a worktree, or both. Dispatching fresh re-implements the task
beside the branch that already holds it; one task accumulated five such branches. One command
per task answers this, and its answer decides what happens next:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/swarm/resume-point.sh <id>          # MERGE · VERIFY · REATTACH · FRESH
```

| it says | what you do |
|---|---|
| **MERGE** — committed, and a `VERIFIED <sha>` note matches the branch head | **no worker.** Put the branch straight on step 8's merge list |
| **VERIFY** — committed, no verdict recorded for this head | **no worker yet.** Run step 7's lenses on that branch's diff now; PASS → step 8, FAIL → dispatch with `--resume <branch>` and the finding list |
| **REATTACH** — a worktree holds uncommitted work | dispatch with `--resume <branch>`; the worker continues *in that worktree* |
| **FRESH** — nothing to adopt (no branch, or a branch with nothing ahead of `main`) | dispatch as below. An empty branch and one that fast-forwarded into `main` are indistinguishable from the ref, so neither is ever reported as "landed": a false "landed" would close work nobody did, a redundant dispatch is prevented by the task's own closed status |

`--resume` attaches the worker to the existing branch (reusing its live worktree if there is
one) and prefixes its prompt with *RESUMING — do not start over*, the commit count, and whether
uncommitted changes exist. It also lists any *other* refs holding work for the task; the sweep
reports those as IN FLIGHT and they are yours to adopt or prune.

**Dispatch every agent with `${CLAUDE_PLUGIN_ROOT}/harness/models/dispatch.sh`, not the Agent tool — and all
`n` through one `fanout.sh` call.** Write each prompt to a file, write the `n` dispatch lines
to a jobs file, then:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/swarm/fanout.sh --jobs <file> --cap <n> --wave <epic>-w<k>     # every job at once, each with a timeout; records `dispatched`
```

It answers for every job — first line and `full:` path each — and a job past its timeout is
**HUNG**, never left to hold the wave. Headless, `--detach` then `--wait <id>` (exit 5 = still
running, call it again). Before it existed you ran `n` background Bash calls in one message
and read `n` rc files; sequential dispatch was the known lapse.

The jobs file, one per line, no shell:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/models/dispatch.sh <agent> --prompt-file <path> --task <id> --worker <n> --lane <lane> --digest
${CLAUDE_PLUGIN_ROOT}/harness/models/dispatch.sh <agent> --prompt-file <path> --task <id> --worker <n> --lane <lane> --digest --resume <branch>   # REATTACH, or VERIFY that failed
```

**`--digest`** prints the report's first lines and the path of the file holding all of it
(`.harness/run/out/`), instead of the whole report. A worker's return line is what you act
on; the lenses read the diff through `brief.sh`, not the worker's account of it. Measured
on a campaign orchestrator: whole subagent results were the largest things in its context
and were re-read on every later turn.

**Why the boundary rather than the Agent tool.** It is what makes the model tier real:
`tiers.yaml` decides the model, the effort and a hard `--max-budget-usd` ceiling per dispatch,
and every call records its own cost, tokens and turns as a `harness.dispatch` event task
(`make models-cost`). The Agent tool can express none of that.

**`--worker <n>` is mandatory for writers** (`fullstack-engineer`, `quality-engineer`). It
creates the worktree AND runs `swarm-worktree-init.sh` inside it, so the worker starts with its
dependencies restored and its own isolated per-worker resources. This is not optional politeness: **`claude -p`
does NOT honour `isolation: worktree` from frontmatter** — measured. Without `--worker` the
dispatcher refuses rather than running a writer in the primary checkout, which is what a whole
wave doing so would corrupt. **Omit `--worker` for the read-only lenses**; they change nothing
and would pay the bootstrap cost for no benefit.

**A denied tool is a FAILURE, even though the CLI calls it success.** Headless `-p` does not
block on a missing permission — it denies the tool, lets the model continue, and returns
`subtype=success`. A worker denied `Bash` cannot run the tests and would otherwise report PASS.
`dispatch.sh` already treats any denial as not-ok and exits non-zero; if you see one, the work
did not happen, whatever the return text claims.

**Exit 3 is a budget kill, and it is not `BLOCKED`.** The worker never got to return
anything; the ceiling cut it off. `dispatch.sh` records the spend it reached, prints the
steps that arrived before the kill and then the error (never the error instead of them), and
says `BUDGET EXHAUSTED` on stderr. Route it deliberately: run `resume-point.sh <id>` first —
a kill mid-edit usually leaves REATTACH-able work — then either escalate the tier (the task
needed more than the ceiling) or split it (the task is too big), and never re-dispatch as-is.
A pipe such as `dispatch.sh … | tail` returns `tail`'s status, so read the exit code from the
rc file, not the pipeline.

**The prompt file is the only parent→child channel.** The worker sees none of
this conversation, none of the files you read, none of the planner's output. Each prompt
must carry, in full:

- the task's complete `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh show <id>` text — description, acceptance criteria, notes,
  including any `ARCHITECTURE:` note
- **that test commands go through `${CLAUDE_PLUGIN_ROOT}/harness/verify/run.sh`, which loads
  `.swarm-env` itself** (the worktree and its env are already prepared by `--worker <n>`; the
  worker does NOT run swarm-worktree-init.sh itself). A worker must never `source .swarm-env`:
  every Bash call is a fresh shell, so the exports would not survive to the next command even
  if `source` were permitted — and it is not, because it evaluates its argument as shell code
  and so matches no permission rule
- **the per-worker environment as `.swarm-env` actually generated it** — read that file
  rather than retyping it; it is derived from the stack modules and the worker number, and a
  hand-typed copy is how a worker ends up sharing a sibling's resources
- the commit protocol and the merge-slot id
- the resource ban list
- the ten-line return contract
- **the task's slice of the epic's `SPEC INDEX`** — the authoritative feature doc and the
  sections that govern this task, the ADRs and settled owner decisions its `SURFACE:` line
  names, with each decision's verbatim answer. **Pointers, never pasted spec prose:** the
  worker must open the doc, because a paraphrase is how a task gets built from a summary. This
  is the single highest-value thing in the prompt — the lens failures that cost this campaign
  most were tasks whose worker never knew which ADR or owner decision bound them.
- **the memory keys you already know are relevant** — from your pre-flight `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh memories`
  scan and the paths this task touches — so the worker can `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh recall <key>` directly
  instead of hunting. You can see across the wave; the worker cannot.
- for a fidelity **fix**: the auditor's measured defect list from the task

## 6. Collect

`BLOCKED`, `SKIPPED` and `NEEDS-SERIAL-LANE` are recorded, not retried in-wave.

## 7. Verification gate — decorrelated lenses

```bash
${CLAUDE_PLUGIN_ROOT}/harness/swarm/lens-gate.sh <id> <sha> --branch <branch> --lane <lane> --worker <n> [--wave <epic>-w<k>]
```

One call per `PASS` claim. It runs **L1–L3 always, L4 when its trigger fires**, and they
stack only because each is allowed to see something different — that is the
decorrelation, not copies of one opinion:

| Lens | Agent | Sees | Owns | When |
|---|---|---|---|---|
| L1 | `verifier` | task + acceptance criteria + **the diff** + **the suite's output** | correctness: every criterion met and located; one clean commit | always |
| L2 | `verifier-tests` | the diff + tests, in the branch's worktree | test quality: adversarial vs decorative, what was skipped | always |
| L3 | `verifier-spec` | the task + **the repo as it now stands — NOT the diff, NOT the worker's report** | docs, specs, ADRs, callers, blast radius, mechanical invariants | always |
| L4 | `verifier-security` | the diff + the repo + **why it fired** | **what the wrong person can now reach**: authz, tenant isolation, data exposure, auth/session, CSRF, injection, secrets | **on trigger** |

| step | what the gate does |
|---|---|
| `brief.sh <id> <sha> --json` | the brief once — task text, **acceptance criteria**, stat, paths by area, **commit hygiene** (one commit ahead? subject names the id? touches the export?), and **the L4 trigger**: `security.paths`, `security.tokens` in *added* lines, the area map, and the task's `SURFACE:` line — a task with no `SURFACE:` line fires L4, because doubt fires |
| worktree | `--branch` attaches to the branch's worktree (reusing the worker's live one). **The lenses used to run in the primary at `main`, before step 8 merged** — L2 re-ran a suite without the change, L3 read a repository without it |
| `run.sh --lane <lane> test` | **the suite, once, where the change is.** Its output goes to a file L1 and L2 read by path. Red or hung → COULD NOT JUDGE: a lens over a red suite is the believe-the-report failure |
| prompts | one per lens; each says to batch with `scan.sh`/`peek.sh`. **L3's names `brief.md` and nothing else** — the brief carries no diff body and no diff pointer, the diff lives in a sibling root, and `verifier-spec` is *denied* that root on the dispatch (`Read(//…/briefs-diff/**)`). Physical, not instructional |
| dispatch | all three or four at once through `dispatch.sh`, each with a timeout; L2 and L3 with `--cwd` the worktree |
| verdicts | one parser. **A lens that hung, was denied a tool, was cut off at its ceiling, or returned no `VERDICT:` line is NONE — never PASS** |
| unanimity | any FAIL blocks. A majority rule would let two lenses outvote the one that actually looked at the thing — these read different sensors, so a disagreement is information, not noise |
| `tk.sh note <id> "VERIFIED <sha12>: L1 PASS · …"` | **only when every lens passed** — the line `resume-point.sh` reads, written by the same code that reads it. A run stopped between here and step 8 resumes at **MERGE** |

Exit 0 PASS · 1 FAIL — nothing written, the route printed · 2 could not judge. Fifteen to
twenty calls at your context's price were one; the L4 trigger, the suite-once rule and
L3's independence are code, not things you remember.

**L4's trigger, and why it is computed.** L4 fires on a declared `security.path`, a
`security.token` in an added line, the area map, **the task's `SURFACE:` line whatever
the diff shows**, and — since doubt fires — the absence of one. In one campaign L4 fired on
a task whose diff touched no declared path and matched no keyword; it fired only because
the orchestrator hand-reasoned that a derived timestamp *was* the access control, and
found a hole letting any authenticated user alter another account's records. That should
never have rested on a judgement call, and it no longer does. `--l4 always` dispatches it
regardless; L4 returns `PASS (no security surface)` cheaply, and a missed leak is not cheap.

**L4 blocks like any other lens.** It carries the same `blocking`/`filed` governor as L3 —
only `blocking` findings can FAIL — with one deliberate exception: a change that makes a
*pre-existing* hole materially easier to reach is `blocking`, because the change is what
put it in reach.

**L3's blocking/filed governor.** L3 tags every finding `blocking` (caused by this change)
or `filed` (pre-existing). **Only `blocking` findings can FAIL a task.** `filed` findings
become `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh create` lines in the wave report. Without this, L3 fails
every task that touches your largest shared module forever, and a lens that always fails
is a lens you learn to ignore.

**The route line is a recommendation; the decision is yours.** On a FAIL the gate prints:

- **L2, or L1 classified test-shaped** — decorative assertion, untested path, weakened or
  skipped test → **`quality-engineer`**, after step 8 merges the branch, in its own
  worktree. The author already missed it once.
- **L1 or L3 `blocking`** — wrong behaviour, unmet criterion, broken caller, stale doc or
  ADR, invariant violation → back to **`fullstack-engineer --resume <branch>`** with the
  finding list.
- **L4 `blocking`** → back to **`fullstack-engineer`**, and **raise the task's priority to
  match the severity**. A `critical` or `high` security finding on a P3 task means the
  task was mis-priced, not that the finding is minor.

Either way the task stays open until the remediation itself passes every lens that ran.
**A third round is not a fourth remediation** — see `/campaign`'s breaker: split the task
along the seams the rounds revealed.

**Cost.** A four-lens round is roughly 480k subagent tokens; the levers that do not trade
fidelity — the brief instead of a 96k-token `git show`, one `scan.sh` where a lens made
31 searches, measurements shared and judgements never — live in `lens_gate.py`'s and
`brief.py`'s docstrings with their measurements. Two rules still bind a lens you dispatch
by hand: **derive every lens's isolated resources from its TASK ID, never from the lens
name** (two concurrent `verifier-tests` runs handed the same fixed name deadlocked on a
unique constraint), and **only L2 executes tests** (the lenses share one set of per-worker
resources; two concurrent runs that reuse a database collide destructively).

A near-zero FAIL rate across the lenses is not reassurance — it means the gate has gone soft.

## 8. Integrate + wave gate — serial, yours alone

```bash
${CLAUDE_PLUGIN_ROOT}/harness/swarm/merge-wave.sh <branch>... --lane <lane> --wave <epic>-w<k>
```

Every passing branch — this wave's, every branch step 5 found at **MERGE**, every VERIFY branch
the gate passed without a worker. Adopted work merges exactly like this wave's.

| step | rule |
|---|---|
| every argument is a ref; the tree is clean | checked **before** the slot is taken; a typo merges nothing |
| `tk.sh slot-acquire` … `slot-release` | the release is in a `finally` — **a slot held by a dead run blocked the next wave forever**, and `/halt` §4 existed for it |
| merge each **from the ref**, ascending by task id, `--no-ff` | never from a worktree you did not just create: one was found holding a **staged revert** of its own fix while `git log` on the branch showed the good commit |
| a conflict | `git merge --abort`, the branch left unmerged with its paths named, **the rest of the wave continues** — never resolved here |
| the gate, **once, on the merged result** | one `run.sh --stack <s> lint typecheck test` per declared stack, concurrently and never split further (type checkers and bundlers each hold 1–2 GB). Every key answers; an undeclared one is `--`; **a stack with nothing declared is not green**. Per-merge gating certifies states that are never pushed — `M1+M2+M3` is the only state that ships |
| red → attributed | the failing paths in the gate's digest → the commits since the wave base that touched them → the task ids their subjects name. `git revert -m 1 <merge sha>` is **suggested, never done**; zero extra suite runs |

**Merge conflicts: never resolved by a worker, and never quietly by you.** A conflict is a
**planning defect first** — step 3's contention check missed a shared path — and its count is
health signal ④. **Default: re-queue.** The task re-dispatches next wave on top of the merged
result; one task = one commit = one small change by construction, so the worker re-derives it
in minutes. Rebase-and-retry is almost always cheaper and safer than a resolution, and it
preserves the authorship-independence principle the whole design rests on — the two authors
are the worst parties to arbitrate, and you chose the wave, so you are the party least
motivated to record the miss honestly. **Only if a re-queue would lose real work** — both
tasks legitimately extend the same file and the second change was large or hard-won — resolve
it yourself as the neutral party, taking the union of both acceptance criteria, state which
hunks came from whom and why, and send the result through every lens before the gate.

**Red → the attribution names the task.** Revert the culprit (the suggested command), re-run
the gate once (`run.sh --stack <s> lint typecheck test`), file a fix task against that id, and
record the attribution in the wave report. Only if the attribution is empty — a cross-task
interaction with no shared path, rare by construction — bisect: `git reset --hard <wave-base>`,
re-merge one at a time, run **only the failing test ids**.

## 8b. Wave-stage code review — quality, once per wave, non-blocking

**Run this on the integrated result, after the gate is green and before you push.** One
`/code-review` pass over the whole wave's diff (`git diff <wave-base>..HEAD`), covering
**efficiency, robustness, reliability, clarity, performance, and consistency across the
codebase**.

**Why the wave and not the task.** Per-task lenses structurally cannot see consistency: two
tasks can each be internally clean while introducing two different idioms for one thing, or
both growing the same megafile, and L1-L4 will each pass them alone. The wave diff is the
smallest unit where "is this consistent with the rest of the codebase" is even answerable.
It is also 1 dispatch per wave instead of 4 per task.

**It does NOT block, and that is deliberate.** Findings become `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh create` lines in the wave
report. A quality reviewer that can block stalls waves on taste; one that cannot block gets
ignored — so the discipline is that **every finding is filed as a task before the push**, not
that it holds the wave. If a finding is severe enough to want blocking, it is a correctness
or security finding and belongs to L1 or L4, not here.

Ask it for, in priority order:

1. **Efficiency and performance** — repeated per-row queries where one would do, work inside
   a loop that belongs outside it, unbounded queries. Your framework module names the specific
   traps for this stack.
2. **Robustness and reliability** — unhandled failure modes, swallowed exceptions, partial
   writes outside a transaction, retry/idempotency gaps.
3. **Clarity** — a reader-hostile construct, a comment that overclaims, a name that lies.
4. **Consistency** — a second way to do something the repo already does one way. This is the
   finding only the wave pass can make.
5. **Exception strategy, across the wave and against the codebase.** L1 already judged
   whether *each* change handles its own exceptions correctly. You judge whether they add up
   to one strategy: is failure signalled the same way at the same layer (raise vs return a
   violation vs return `None`), does each layer own the same slice (services raise domain
   errors, routers map them to status codes, jobs log and retry), is the exception hierarchy
   coherent or has a task invented a parallel class for an existing case, and do two tasks in
   this wave now handle the same condition two different ways. Name the convention the repo
   already has before calling something inconsistent with it.
6. **Logging — is it a usable basis for debugging and observability?**
   - **Level matches meaning.** An invariant violation logged at `info` is invisible when it
     matters; a routine event at `error` trains people to ignore errors.
   - **Enough context to act on, without PII.** A log line naming an operation but not the
     entity ids it acted on cannot be traced; one carrying personal data is a privacy defect
     against your declared `security.invariants`. Prefer ids.
   - **Consistent shape.** The same event should log the same way everywhere. If the repo is
     converging on one format, new lines must not entrench a second.
   - **The silent paths.** A branch that swallows, degrades or falls back and logs *nothing*
     is the one that costs hours later. Flag it even when the swallow itself is correct.

**The accretion check is signal ①c** — `wave-report.sh` (step 10) computes it from the
manifest's wave base against `signals.megafile_lines`. A file past the threshold that grew in
this wave while its decomposition task sat untouched is the campaign quietly making the
problem worse, and the number belongs in the wave report where it is visible — not discovered
a year later. (This used to be a bash `for` loop pasted here with `${MEGAFILE:-1000}` where
the declared threshold should have been read.)

## 9. Tasks sync and push — once per wave, never per worker

```bash
${CLAUDE_PLUGIN_ROOT}/harness/swarm/close-wave.sh <id>="<what shipped, how verified>" <id>="…" --restore-autosync
```

| step | what it does |
|---|---|
| `tk.sh close <id> --reason …` | one per task, **ascending by id**; a failure stops here with the rest listed by hand |
| `tk.sh export` | **always writes** — `bd export` without `-o` streamed to stdout and wrote nothing, and the tracked file kept showing a closed record as `in_progress` while `git status` looked clean |
| `render-epic.sh <epic> --write …/tasks.md` | the epic's readable view, for every epic the closed tasks belong to; `--check` first, so a hand-edited view is said on the line before it is regenerated |
| `git add -- <the tracked export> <views>` · `git commit` | the export the backend declared, nothing wider (`.beads/` also holds the `config.yaml` that `autosync off` rewrote) |
| `git pull --rebase --autostash` | `--autostash`, because that `config.yaml` is unstaged on every beads run and a plain rebase refuses to start over it. **If the rebase pulled in someone else's commits the sequence STOPS before the push** — re-run step 8's gate on the rebased tree, then `git push` |
| `git push` · `git status -sb` | the push is the wave's terminal action; `ahead`/`behind` afterwards is a failure |
| `tk.sh autosync on` | **only with `--restore-autosync`** — a standalone `/swarm` is the whole run and restores what its pre-flight disabled. Inside `/campaign` §4 the flag is omitted: re-enabling it mid-campaign would have the backend staging its export into the next wave's worker commits, and §5 restores it |

Ten calls at your context's price were one, and the order can no longer be got wrong.
`--no-push` stops after the commit; `--check` verifies every id is open and writes nothing;
`--sync-only` is the export-commit-push tail with no closes.

**The push is the wave's terminal action**, matching `/grind`, which pushes per task. A wave
is a complete, gated unit of work — it has passed every applicable lens, a whole-repo wave
gate and the wave-stage code review — so leaving it local strands it for no benefit. If the
push fails, resolve and re-run `close-wave.sh --sync-only --restore-autosync`.

## 10. Report, then offer the next wave


```bash
${CLAUDE_PLUGIN_ROOT}/harness/swarm/wave-report.sh <epic>-w<k>       # the four signals, computed, with direction against the previous wave
```

Tasks completed / filed / blocked, the wave-gate result, the report's table, and a fresh
`${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh ready --label <lane>`.

**To stop mid-wave**, see `/halt`: `Esc` interrupts the orchestrator, `/tasks` stops
individual in-flight workers, and `/halt` cleans up after — claims, the merge slot,
uncommitted worker edits, and `export.auto`.

**The four numbers are computed every wave and written to the manifest; what each one MEANS
is yours to say.** A signal you do not write down is not a signal — and until 0.10.22 nothing
wrote them down.

| # | Signal | Bad value, and what it means |
|---|---|---|
| ① | **First-pass PASS rate** — tasks clearing every applicable lens first time | above `signals.baselines.first_pass_ceiling` sustained → the gate has gone soft. Below `first_pass_floor` → the *tasks* are underspecified; fix the planner, never the worker (a worker cannot ask a question) |
| ①b | **L4 dispatch rate** — rounds touching a declared `security.path` where L4 never fired | a wave that changed an endpoint without dispatching L4 has a broken trigger, not a clean record |
| ①c | **Megafiles that grew** | any file past `signals.megafile_lines` growing while its decomposition task sits untouched |
| ② | **Escape rate** — `fix:`/`revert:` share of the last 200 commits, against `signals.baselines.escape_rate` | rising materially above your recorded baseline, or **any `revert:` of a swarm commit**. This is the defect the gate missed, and the number that says whether L3 earned its keep. No baseline yet → the report says so; record the first measurement |
| ③ | **Changed lines per satisfied acceptance criterion** | any single task over ~400 → the "one task = one reviewable change" slicing rule has slipped. Published agent benchmarks show *less* code for the same grade, so growth here is a warning, not throughput |
| ④ | **Wave yield** (dispatched → closed same wave) **+ merge conflicts** | **<60% yield**, or **any non-zero conflict count** — a conflict is a step-3 miss by definition. The shape of the losses names the fix: contention drops → step 3; `SKIPPED already claimed` → stale queue; `NEEDS-SERIAL-LANE` → planner ignored the singletons; `BLOCKED` → the split-brain pass is not running |

**Cost proxy.** `make models-cost` reports real per-dispatch cost from the boundary's own
telemetry. If a wave costs materially more than the same tasks under `/grind` without a quality
gain, the model tier is the lever: the writer agent dominates token spend, so that is where an
experiment starts.
