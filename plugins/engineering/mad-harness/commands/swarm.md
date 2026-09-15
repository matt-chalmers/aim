---
description: Run one wave of parallel workers over the ready queue for a lane, with a serialized commit phase, a whole-repo wave gate, and a push at wave end
argument-hint: "[lane — one declared in harness.yaml] [n: 1-4]"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/harness/*), Task, Bash(git:*), Bash(make:*), Read, Glob, Grep, AskUserQuestion
---

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
git status --porcelain          # must be clean
${CLAUDE_PLUGIN_ROOT}/harness/checks/check-project-config.sh --strict   # exit 3 = the plugin moved on since this config was reviewed: stop, run /harness-setup
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh slot-check             # must exist and be free
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh autosync off # stop beads staging issues.jsonl into a sibling's commit
                                # (step 9 restores it — if a run dies before then, /halt does)
${CLAUDE_PLUGIN_ROOT}/harness/checks/check-ports.sh   # declared `ports:` already bound — a server left over from a killed run
df -h . | tail -1                          # disk headroom — worktrees consume it (informational)
git worktree list && git worktree prune   # worktrees stranded by a previous killed run
${CLAUDE_PLUGIN_ROOT}/harness/swarm/worktree-sweep.sh                # then the real sweep — see below, prune alone is a no-op
${CLAUDE_PLUGIN_ROOT}/harness/checks/check-stack-commands.sh --repair   # do the declared commands still work?
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh memories                     # scan the field-guide index for this wave's subject matter
```

**`git worktree prune` does not do this job.** It only forgets worktrees whose *directory is
already gone*, so it is a no-op against the ones that actually accumulate — one per dispatched
task, for the whole run. A campaign reached **35** before this was noticed.

Run **`${CLAUDE_PLUGIN_ROOT}/harness/swarm/worktree-sweep.sh`** (dry run) and then `--apply`. It classifies rather than
deletes, on one rule: **the branch ref is the authority, not the worktree.** A committed
worktree is redundant with its ref — `git archive <branch>` reproduces it exactly — so the
directory can go. Uncommitted work is not redundant, and is never touched:

| class | action |
|---|---|
| branch merged into `main` | remove worktree **and** delete the branch (`-d`, which refuses if it is not really merged) |
| committed, branch unmerged | remove worktree, **keep the branch ref** — it holds the work |
| uncommitted work, **including untracked files** | **report only, never remove**, whatever flag you pass |
| detached HEAD | **report only** — no ref holds those commits, so removing them loses them |
| touched in the last 30 min | **skipped** — an agent may still be working in it |

**Two incidents on 2026-08-27 are why it is shaped this way, and both are worth knowing:**

- A worktree for a task under remediation held a **staged revert** — 7 insertions, 261
  deletions, removing five tests *by name* — while `git log` on the branch still showed the
  good commit. Committing from that directory would have silently undone the work.
  **Merge from the branch ref, never from a worktree you did not just create.**
- The same sweep found one worktree holding the **only** copy of an uncommitted change. A
  blind `git worktree remove --force` loop would have destroyed it.


**Do not run `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh prime` here or give it to a worker.** Its session-close protocol says
`[ ] 4. git push … Work is not done until pushed`, and a worker must never push — it commits
inside the merge slot and you own integration. `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh memories` is the index;
`${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh recall <key>` pulls a body.

Report what is listening. If another session is mid-edit in this checkout, say so and
stop — a swarm on a dirty tree loses work.

## 2. Compute the wave

`${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh ready --label <lane> --limit 20 --json`, take the top `n` by priority, clamp to the cap.

## 3. Contention re-check — the last line of defence

For each candidate, pull the paths named in its description and notes. **If two candidates
share a path, drop the lower-priority one from this wave and say so.** The planner should
have caught it; this is the backstop.

**Megafile check.** `wc -l` every candidate path. **Any file past `signals.megafile_lines`
is a lane of
width 1 for this wave** — at most one task may touch it, however disjoint the functions look.
Name which task won the file and which was deferred. This is the check the dependency graph
physically cannot do: `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh validate` has rated an epic 11-wide when five of its six wave-1
tasks touched one very large shared file. The threshold is `harness.yaml` → `signals.megafile_lines`.

**Shared-vocabulary check.** Read the candidate descriptions side by side and name any
**shared new thing** — the same new field, error code, decision-record number, component or schema.
If two tasks would each *introduce* it, one must **consume** it instead: drop the second and
say so. Same failure class as a shared path, different surface.

**New-file check — the one the path sweep structurally cannot do.** Every check above
operates on files that **already exist**: you pull the paths a task names and `wc -l` them. So
two tasks that each *create* the same new file collide **invisibly** — no description names it,
and the sweep returns nothing.

Ask of every candidate: **what will this task create?** Then compare the answers. The usual
suspects, in order of how often they collide:

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

**And verify mechanically before you merge anything** — `git merge-tree` is instant, needs no
worktree, and has no side effects:

```bash
git merge-tree --write-tree <branchA> <branchB>     # exit 0 = clean
git merge-tree <base> <branchA> <branchB> | grep -c '<<<<<<<'
```

It reports `added in both` / `changed in both` by path. Run it at **step 3** against your
predicted paths if you can, and **always at step 8** against the real branches before the first
merge — a conflict discovered mid-merge costs a re-plan; the same conflict discovered here
costs one line of the wave report.

## 4. Confirm

`AskUserQuestion` with the task ids, titles, the agent each maps to, the files each will
touch, and the `SWARM_DB` assigned.

## 5. Dispatch — through the harness boundary, all `n` in a SINGLE message

**Dispatch every agent with `${CLAUDE_PLUGIN_ROOT}/harness/models/dispatch.sh`, not the Agent tool.** Write each
prompt to a file, then run all `n` in ONE message as background Bash calls — otherwise they
run sequentially and you have gained nothing.

```bash
${CLAUDE_PLUGIN_ROOT}/harness/models/dispatch.sh <agent> --prompt-file <path> --task <id> --worker <n> --lane <lane>
```

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

Dispatch **L1-L3 in parallel** per `PASS` claim, plus **L4 when its trigger fires**. They
stack only because each is allowed to see something different — that is the decorrelation,
not copies of one opinion:

| Lens | Agent | Sees | Owns | When |
|---|---|---|---|---|
| L1 | `verifier` | task + acceptance criteria + **the diff** + **L2's suite result** | correctness: every criterion met and located; one clean commit | always |
| L2 | `verifier-tests` | the diff + tests, and runs them | test quality: adversarial vs decorative, what was skipped | always |
| L3 | `verifier-spec` | the task + **the repo at HEAD — NOT the diff, NOT the worker's report** | docs, specs, ADRs, callers, blast radius, mechanical invariants | always |
| L4 | `verifier-security` | the diff + the repo | **what the wrong person can now reach**: authz, tenant isolation, data exposure, auth/session, CSRF, injection, secrets | **on trigger** |

**L4 fires when the diff touches anything your project declares as a security surface** —
`harness.yaml` → `security.paths` and `security.tokens` — plus **a new or changed response
shape**, code reading or writing another user's data, client-side auth or token handling, or
anything naming one of the declared `security.invariants`.

**And L4 fires on the task's `SURFACE:` line, whatever the diff shows.** If the planner recorded
that this task touches authorization, data exposure, the integrity of a published record, or
any declared privacy invariant, dispatch L4 — even when the path grep is zero.

**This is the half that used to depend on the orchestrator noticing.** In one campaign L4 fired
on a task whose diff touched no declared security path and matched none of the keywords — it
fired only because the orchestrator hand-reasoned that a derived timestamp *was* the access
control. It then found a hole letting any authenticated user alter another
account's records. That should never have rested on a judgement call, and with
`SURFACE:` it does not.

**Compute the trigger mechanically, from `git diff --name-only` plus a grep of the diff
body, plus the task's `SURFACE:` line — never from the worker's summary.** A worker that did not realise it touched a
security surface is exactly the case L4 exists for. When in doubt, dispatch: L4 returns
`PASS (no security surface)` cheaply, and a missed leak is not cheap.

**L4 blocks like any other lens.** It carries the same `blocking`/`filed` governor as L3 —
only `blocking` findings can FAIL — with one deliberate exception: a change that makes a
*pre-existing* hole materially easier to reach is `blocking`, because the change is what put
it in reach.

### Cost discipline — four levers, none of which trades fidelity

A four-lens round costs roughly 480k subagent tokens. On a 25-line change that is the wrong
shape, and the fix is not to weaken a lens — it is to stop paying for work already done.

**0. Build the brief ONCE, before dispatching any lens.**

```bash
${CLAUDE_PLUGIN_ROOT}/harness/verify/brief.sh <task-id> <commit-sha>     # prints the path to brief.md
```

Four lenses otherwise each re-derive the same diff stat, changed-file list and task
text. Measured on a 44-file commit: the full diff is **~96,000 tokens**, the brief
**~1,700** — and both lens dispatches measured in the parity exercise were 100% bash
at ~2,600 tokens per call, so this is the largest single line item on the gate.

- **`brief.md` goes to all four lenses.** It carries measurements only and **no diff
  body**, which is what makes it safe for L3.
- **The `diff/` artefacts go to L1, L2 and L4 by path — never to L3.** Give them
  `diff/by-file/<slug>.patch` for the files their lens actually reasons about;
  `full.patch` exists but reading it costs the whole saving.
- **Never `git show <sha>` in a lens prompt.** That is the 96,000-token path.

**0b. Batch the questions. One call, many answers.**

Measured across four real lens runs: 92 bash calls, of which 31 were searches, 16
were slice reads and 11 were reads at a revision — **58 of 92 asking one small
question each**, at ~2,600 tokens of call overhead apiece. Two primitives collapse
them:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/verify/scan.sh -e 'PAT' -e 'PAT' -e 'PAT' [--rev SHA] [pathspec...]
${CLAUDE_PLUGIN_ROOT}/harness/verify/peek.sh path:10-40 other/file.py:1-25 third.md [--rev SHA]
```

Put **every** search you expect to run into one `scan.sh`, and every file or slice
you already know you want into one `peek.sh`. Both answer every input — a pattern
with **zero hits is reported as searched-and-found-nothing**, and a missing path
gets `!! not found` — so a batch never leaves you reasoning about an answer you
did not actually receive.

State this in the dispatch prompt. A lens that is not told to batch will not.

**1. The dispatch prompt carries only what the task does NOT.** The task already holds the
`SURFACE:` line, every acceptance criterion and its notes; say "read the task" and add only
the two or three things that are not in it — the branch, the commit, and any trap specific to
this diff.

**The reason is scope, not prompt length — and the old rationale here was wrong.** This
section used to claim prompt text is re-processed on every tool call, so a 1,200-word prompt
across 40 calls is "paid forty times", and called that the cheapest saving available. It is
not, and measurement says so: across 24 lens dispatches in one campaign, cost fits
`tokens ~= 18,700 + 2,600 x tool_calls`, and **tokens-per-call FALLS as calls rise** (3,397 at
<=30 calls, 2,825 at 45+). Were the prefix re-processed at full price that curve would bend the
other way; it bends down because the accumulated conversation is served from cache. Trimming
the prompt saves a fraction of one call.

**What the same data says the lever actually is: tool calls.** At ~2,600 tokens each, halving
a lens's calls is worth ~44%. And splitting one lens into two is worth **-11%** — each dispatch
re-pays the ~18,700 fixed base, and you lose the cross-cutting read that catches a code change
falsifying a task's text.

So keep the prompt tight because a shorter prompt is a **clearer** one, and spend the saving
where it exists: give the lens a precomputed brief so it need not re-derive the diff stat, the
changed-file list and the task text that its three sibling lenses are deriving in parallel.

**2. Share MEASUREMENTS between lenses. Never share JUDGEMENTS.** Decorrelation is about what
each lens **sees of the change** — L3 must never receive the diff or the worker's report, and
that rule does not bend. It is *not* about re-measuring settled mechanical facts. If L1 has
already run the linter and reported "4 files already formatted", tell L2 so and let it spend its
budget on tests instead. Hand over *numbers and greps*, never "L1 thinks this is fine".

**3. A mutation log from `${CLAUDE_PLUGIN_ROOT}/harness/verify/mutate.sh` is a durable artefact — verify it, do not redo
it.** That harness self-attests: it aborts if a mutation does not match exactly once, restores
by re-extracting rather than undoing, records failing test **names** per mutant, and re-runs
mutant 1 last, failing the batch if the result moved. So L2's job on a worker-produced log is
to confirm its provenance (right sha, right harness) and **spot-re-run three of sixteen**, not
to re-run all sixteen. Re-run the full set only when the log is absent, hand-rolled, or its
recheck line is missing.

**4. L4 is already trigger-based; L2 may be scoped on a task that ships no logic.** A
docs-only or pure-re-pin task has nothing for a test-quality lens to judge beyond "the suite
is still green", which the wave gate already proves. **L1 and L3 stay unconditional** — and L3
especially, because the defect it catches most often is line-pin drift, which happens on *any*
commit that shifts lines regardless of what the task was about.

**Derive every lens's isolated resources from its TASK ID, never from the lens name.** Two
concurrent `verifier-tests` runs handed the same fixed name deadlocked on a unique constraint;
the lens noticed, discarded its numbers and re-measured on a fresh one — costing a full
both-ends re-run, and it only caught it because the deadlock happened to surface. A silently
interleaved run would have produced *plausible wrong counts*, which is the failure this whole
pipeline is built to avoid. `<slug>_l2_<task-slug>` cannot collide; "remember to vary it"
already failed once.

**Only L2 executes tests.** L1, L3 and L4 must not run the suites: the lenses are dispatched in
parallel *without* worktree isolation, so they share one set of per-worker resources, and two
concurrent runs that reuse a database collide destructively. Dispatch L2
first and pass its verbatim counts into L1's prompt — or, if you want all three strictly
simultaneous, run the scoped suite yourself once and paste that output into L1's prompt.

**Do not hand L3 the diff or the worker's report.** If you do it anchors on them and
re-derives L1, and you have paid for three lenses and bought two. L3 is the only lens that
can catch a worker *describing* something it did not build.

**Unanimity to pass: any FAIL from any lens — including L4 — blocks the task.** A majority rule would let two
lenses outvote the one that actually looked at the thing — and these read different sensors,
so a disagreement is information, not noise. `verifier.md` already argues the principle: a
false PASS is far more expensive than a re-run.

**L3's blocking/filed governor.** L3 must tag every finding `blocking` (caused by this
change) or `filed` (pre-existing). **Only `blocking` findings can FAIL a task.** `filed`
findings become `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh create` lines in the wave report. Without this, L3 fails every task that
touches your largest shared module forever — thousands of lines of pre-existing everything —
and a lens that always fails is a lens you learn to ignore.

**Route each FAIL by which lens raised it:**

- **L2 (test-shaped)** — decorative assertion, untested path, weakened or skipped test,
  missing edge case → **`quality-engineer`**, after step 8 merges the branch, in its own
  worktree. The author already missed it once.
- **L1 or L3 `blocking`** — wrong behaviour, unmet criterion, broken caller, stale doc or
  ADR, invariant violation → back to **`fullstack-engineer`** with the finding list.
- **L4 `blocking`** — authorization, isolation, exposure, auth/session, injection, secrets →
  back to **`fullstack-engineer`**, and **raise the task's priority to match the severity**.
  A `critical` or `high` security finding on a P3 task means the task was mis-priced, not
  that the finding is minor.

Either way the task stays open until the remediation itself passes every lens that ran.

A near-zero FAIL rate across the lenses is not reassurance — it means the gate has gone soft.

## 8. Integrate + wave gate — serial, yours alone

**Merge every passing branch first, then gate the result once.** Inside a single
`${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh slot-acquire` / `release`, merge each passing branch in ascending task-id order,
recording `git rev-parse HEAD` after each so you have an ordered list `M1…Mn`. **Run no suite
between merges.**

Then run the gate **once**, on the merged result, on the **default** `DB_NAME` — as **two
Bash calls in a single message**, so the halves run concurrently:

Issue **one Bash call per stack**, and let the harness compose it — the commands come
from that stack's config, so nothing in this file has to know which runner your project
uses:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/verify/run.sh --stack <name> lint typecheck test
```

For a two-stack repo that is two calls. Every key you name produces a line, **including
one the stack does not declare** — reported as `--`, never silently skipped, so a gate
cannot look green because a step quietly did not run.

**Every call must be green — one green and one red is a red gate.** Separate stacks share no
file, port, database or process, so running them concurrently is a scheduling change only.
Issue them in *one* message; separate messages run serially and you have paid for the
serialisation you were removing. **Do not split further than one call per stack** — type
checkers and bundlers each hold 1–2 GB, and four at once is where a machine dies.

Gating once rather than per merge is not a weakening. Per-merge gating certifies `M1` and
`M1+M2` — states that are never pushed and do not survive the wave. `M1+M2+M3` is the only
state that ships, and it is gated identically. Per-merge is arguably *weaker*: it invites
treating "M1 was green" as evidence about M1's task, when that task actually ships inside the
full merge, where an M1↔M3 interaction defect is invisible to the M1 gate.

**Red → attribute before you re-run anything:**

1. **Read the failure.** The gate names the failing test and file;
   `git log --oneline -- <failing path>` names the task. This is unambiguous by construction —
   step 3 guarantees no two tasks in a wave share a path, and every task is one commit with
   path-explicit staging. **Zero extra suite runs**, and it resolves most reds.
2. **Only if that is ambiguous** (a cross-task interaction with no shared path — rare, by the
   same construction): `git reset --hard <wave-base>`, re-merge `M1`, and run **only the
   failing test ids** — seconds, not the suite. Add one merge at a time until it goes red.
3. Revert the culprit, re-run the gate once, and file a fix task against that task id. Record
   the attribution in the wave report; it is health-signal ④ evidence.

**Merge conflicts: never resolved by a worker, and never quietly by you.**

- A conflict is a **planning defect first** — step 3's contention check missed a shared path.
  **Record which path in the wave report**; that count is health signal ④.
- **Default: abort and re-queue.** `git merge --abort`, leave that branch unmerged, and
  re-dispatch the task next wave on top of the merged result. One task = one commit = one
  small change by construction, so the worker re-derives it in minutes. Rebase-and-retry is
  almost always cheaper and safer than a resolution, and it preserves the
  authorship-independence principle the whole design rests on — the two authors are the worst
  parties to arbitrate, and you chose the wave, so you are the party least motivated to record
  the miss honestly.
- **Only if a re-queue would lose real work** — both tasks legitimately extend the same file
  and the second change was large or hard-won — resolve it yourself as the neutral party,
  taking the union of both acceptance criteria, and state which hunks came from whom and why.
  Then send the result through every lens that applies before the gate, like any other change. Whole-repo checks belong here and nowhere else — run inside a
worker they produce phantom failures from siblings' in-flight edits.

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

**Plus a mechanical accretion check — two lines, no agent:**

```bash
for f in $(git diff --name-only <wave-base>..HEAD); do
  [ -f "$f" ] && [ "$(wc -l < "$f")" -gt "${MEGAFILE:-1000}" ] && echo "MEGAFILE GREW: $f now $(wc -l < "$f")"
done
```

Report every hit. A file past `signals.megafile_lines` that grew in this wave while its
decomposition task sat untouched is the campaign quietly making the problem worse, and the
number belongs in the wave report where it is visible — not discovered a year later.

## 9. Tasks sync and push — once per wave, never per worker

```bash
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh close <id> --reason "<what shipped, how verified>"   # one per closed task
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh export
# The epic's readable view, if this wave belongs to one. GENERATED — never hand-edited.
${CLAUDE_PLUGIN_ROOT}/harness/tracker/render-epic.sh <epic> --write <paths.proposed>/<epic>-<slug>/tasks.md
git add <the tracked export>  && git commit -m "chore(tracker): close <ids>"
git status --porcelain <the tracked export>   # must be clean once committed
git pull --rebase
git push
git status -sb                                   # must show up to date with origin
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh autosync on                   # restore what pre-flight disabled
```

`export` ALWAYS writes, which is a change: `bd export` without `-o` streamed to stdout and
wrote nothing, so the tracked file silently kept showing a closed record as `in_progress`, and
`git status` can look clean because the on-disk file matches HEAD. Skip it and you publish a
backlog that disagrees with the code. `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh close <id> "msg"` is wrong; it needs `--reason`.

**The push is the wave's terminal action**, matching `/grind`, which pushes per task. A wave
is a complete, gated unit of work — it has passed every applicable lens, a whole-repo wave
gate and the wave-stage code review — so
leaving it local strands it for no benefit. **If the rebase pulls in someone else's commits,
re-run the wave gate before pushing.** If the push fails, resolve and retry until it
succeeds.

## 10. Report, then offer the next wave


Tasks completed / filed / blocked, wave-gate result, and a fresh
`${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh ready --label <lane>`.

**To stop mid-wave**, see `/halt`: `Esc` interrupts the orchestrator, `/tasks` stops
individual in-flight workers, and `/halt` cleans up after — claims, the merge slot,
uncommitted worker edits, and `export.auto`.

**Report these four numbers every wave, and say which direction each moved.** A signal you do
not write down is not a signal.

| # | Signal | Source | Bad value, and what it means |
|---|---|---|---|
| ① | **First-pass PASS rate** — tasks clearing every applicable lens first time | the gate | above `signals.baselines.first_pass_ceiling` sustained → the gate has gone soft. Below `first_pass_floor` → the *tasks* are underspecified; fix the planner, never the worker (a worker cannot ask a question) |
| ①b | **L4 dispatch rate** — waves touching a declared `security.path` where L4 never fired | the trigger | a wave that changed an endpoint without dispatching L4 has a broken trigger, not a clean record |
| ①c | **Megafiles that grew** (step 8b) | the accretion check | any file past `signals.megafile_lines` growing while its decomposition task sits untouched |
| ② | **Escape rate** — `fix:`/`revert:` share of the last 200 commits | `git log --oneline -200 \| grep -ciE '^[0-9a-f]+ (fix\|revert)'`, against `signals.baselines.escape_rate` | rising materially above your recorded baseline, or **any `revert:` of a swarm commit**. This is the defect the gate missed, and the number that says whether L3 earned its keep |
| ③ | **Changed lines per satisfied acceptance criterion** | `git show --stat <sha>` | any single task over ~400 changed lines → the "one task = one reviewable change" slicing rule has slipped. Published agent benchmarks show *less* code for the same grade, so growth here is a warning, not throughput |
| ④ | **Wave yield** (dispatched → closed same wave) **+ merge conflicts** | this report | **<60% yield**, or **any non-zero conflict count** — a conflict is a step-3 miss by definition. The shape of the losses names the fix: contention drops → step 3; `SKIPPED already claimed` → stale queue; `NEEDS-SERIAL-LANE` → planner ignored the singletons; `BLOCKED` → the split-brain pass is not running |

**Cost proxy.** `make models-cost` reports real per-dispatch cost from the boundary's own
telemetry. If a wave costs materially more than the same tasks under `/grind` without a quality
gain, the model tier is the lever: the writer agent dominates token spend, so that is where an
experiment starts.
