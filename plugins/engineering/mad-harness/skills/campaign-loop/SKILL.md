---
name: campaign-loop
description: The shared epic-iteration procedure behind /campaign and /campaign-auto — find open epics, design, plan, swarm, verify, document, push, next. Parameterised by MODE (interactive or auto). Not invoked directly; the two campaign commands set MODE and follow this.
---

# Campaign loop

Iterate the open epic queue: **triage → design → plan → swarm waves → verify → document →
push → next epic**, until the queue is empty — one epic per session in `MODE=interactive`,
for the reason §6 gives.

You are the orchestrator for the whole run. You own every singleton resource — the ports your
stacks bind, the shared development database, the reseed and end-to-end targets, any code
generation step — plus the whole-repo wave gate, all merges, and all pushes. Workers own none
of them.

**`MODE` is set by the invoking command** — `interactive` or `auto`. It changes exactly two
things: what happens at the design gate and the plan gate. Everything else is identical.

---

## The hard line — both modes

**Never answer a `decision` task.** If the architect, the planner or a worker surfaces a
genuine product, spec or design question, you do not resolve it and you do not guess:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh create "<the question>" -t decision -p 1 --description "<options and trade-offs>"
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh park <epic-id> --reason "<one line: what decision is owed>"     # the gate AND the status, one verb
```

**`park` is two steps the tracker owns, not one the loop remembers.** `gate create <epic>`
alone does NOT park an epic on beads — it refuses the blocking edge (*"epics can only block
other epics, not tasks"*) while still writing the gate issue, so the epic stayed in the open
queue. This section used to say "gate create AND `--status blocked`, both steps, always";
`/campaign-auto` shipped with only the first, and `campaign.sh`'s queue would have re-dispatched
the epic forever. `park` does the gate, a `PARKED <gate>:` note, and the status; `unpark`
is its mirror (`${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh unpark <epic-id>`). The port's docstring holds the rest.

The epic then disappears from the queue until the owner un-parks it. **Move to the next
epic.** This is the rule that makes an unattended run safe: the harness is
emphatic that spec and design calls belong to the owner, and a subagent cannot ask a
question. Parking is always better than deciding.

---

## After a compaction — the rules that must survive it

This skill reaches you as a message, and a message is what compaction summarises away.
Measured: one campaign session's summary kept 2 of the 9 task ids the previous 400 rows
named and dropped two that were mid-dispatch. So the harness does not rely on the summary.
A `SessionStart` hook (`compact|resume|startup`) prints the **pinned state** — claims,
merge slot, worktrees, the block below — read from the tracker and git, and names every
pinned id the summary dropped. When you see it, trust it over the summary, and re-read
this skill's §4 before your next action.

<!-- PINNED -->
- **Never answer a `decision` task.** File it, `tk.sh park <epic> --reason …`, move on. `unpark` is the mirror.
- **One epic at a time.** §3 → §4 waves → §5 close → §6 report → next. Never fan §3 across the queue.
- **Every dispatch goes through `dispatch.sh --worker N`** into a worktree; never edit the primary checkout during a wave.
- **No close without the lens gate** — L1–L3 always, L4 when its trigger fires; unanimity to pass.
- **Ask `resume-point.sh <id>` before dispatching any claimed task.** A branch with commits is MERGE/VERIFY/REATTACH, never FRESH.
- **`tk.sh release` what you stop, `preserve-worktrees.sh` before you remove.** Uncommitted work is reported, never deleted.
- **Workers never push. You push once per wave, at `/swarm` step 9,** after the lens gate and the whole-repo wave gate — the push is the wave's terminal action, never a mid-wave one.
- **`tk.sh autosync on` at §5**, or in `/halt` if the run dies first.
<!-- END PINNED -->

The block above is what `${CLAUDE_PLUGIN_ROOT}/harness/swarm/pinned.sh` prints. It is the digest; the sections
it points at are the rule. Edit it when a rule changes and nowhere else.

---

## 0. Pre-flight — once per run

The orchestrator card at the top of the command you ran states the three rules that make
your context affordable; `pinned.sh` prints it again after every compaction. This loop is
where they bite hardest — the scripts below exist for rule 2, `--digest` and `--file` for
rule 3 — and one more thing: load `evidence-gathering` once now (one `Skill` call). Its
batch primitives were written for agents at a sixth of your cost.

```bash
# NOT ${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh prime — the SessionStart hook already ran it; a second call just duplicates
# tens of thousands of characters in your context for nothing.
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh memories        # the field-guide index — content, not a gate
${CLAUDE_PLUGIN_ROOT}/harness/swarm/preflight.sh            # ONE call, eleven steps; exit 0 ready, 3 config upgrade, 1 otherwise
```

`preflight.sh` is `/swarm` step 1's table: clean tree, reviewed config, free merge slot,
**`autosync off`** (§5 restores it; `/halt` if the run dies first), ports, disk — then
`git worktree prune`, the **worktree sweep** (dry run as a gate, then `--apply`),
`check-stack-commands.sh --repair`, and the record-size check. It was six calls at your
context's price and then four more the loop ran after them, the sweep's IN FLIGHT count
read from its text and judged by hand.

**Exit 3 from the config check is a stop, in both modes.** It means `claude plugin update`
has run since `harness.yaml` was last reviewed — the config is stamped with an older
version, or none — and the blocks this plugin reads may simply not be there. Nothing
downstream is trustworthy on an unreviewed config, and there is no safe default to park
it behind. Say so and stop; the owner runs `/harness-setup`, which applies the upgrade
notes and re-stamps. A campaign resumed after that starts here again.

**IN FLIGHT > 0 is a stop.** A previous run halted between a worker's commit and the
orchestrator's merge, and the sweep found the ref: committed work for an open task that
no worktree holds. Re-dispatching that task starts it from scratch beside the branch that
already holds it (one task grew five). Adopt each ref the line names — `resume-point.sh
<id>`: merge it, or dispatch the task *from* it — before §1. The sweep's classification
tables, and the two 2026-08-27 incidents that shaped them (a worktree holding a **staged
revert** of its own fix; another holding the **only** copy of an uncommitted change), live
in `worktree-sweep.sh`'s own header. **Merge from the branch ref, never from a
worktree you did not just create.**

**`--repair` is not a gate.** When a declared command has rotted, the check derives a
replacement from what the repository already declares — a CI step, a package script, a
build target — proves it by running it, and records it in `harness.yaml`. It stops the run only
when **no** working command can be found, because halting a campaign over a renamed script
costs more than it saves. A repair is a real config change: the pre-flight line says
**harness.yaml CHANGED**, and it is worth reading before you commit it with the wave. It
writes `commands` and nothing else. `security`, `signals`, `testing.coverage` and the lane
caps are yours — a mechanism able to edit those is one a worker could use to switch off
the lens about to judge it.

**The record-size line is the ceiling warning.** Past ~64KB `tk.sh note` HARD-FAILS with no
warning — a 38-character append fails exactly as a 3KB one does, and it fails CLOSED. The
check answers in seconds; the inline `for` over `show` it replaced timed out at ~200 tasks
and silently did not happen.

**`${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh prime` is for you only. Never pass it to a worker** — its session-close protocol says
`git push`, and every worker's contract forbids pushing. Workers get `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh memories` instead.

If another session is mid-edit in this checkout, **stop and say so.** A campaign on a dirty
tree loses work.

**Any task over ~50KB is a warning, not a curiosity.** It means the next lens round cannot be
recorded on it, and it is telling you the slicing rule already slipped — one task hit the wall
in one campaign after spanning five concerns across four files. If such a task is in this run's queue, **split it before
dispatching it**, and put its reports in commit messages meanwhile. Do **not** trim its existing
notes to make room: those are the audit trail the lenses built, and rewriting them to fit a
column loses exactly the provenance the next campaign needs.

**No memory or swap gating.** Operating systems grow and reclaim swap on demand — a *total*
swap figure is not a ceiling and the *free* figure self-heals (observed climbing and falling
by gigabytes inside a single session). Memory pressure is likewise a normal state under load, not a
failure. **Never refuse to start a run, and never stop one, on a memory, swap or pressure
reading.** If a wave runs long on a loaded machine, that is expected — record it against the
health signals and carry on.

**The phase heartbeats are written for you.** Each wave script notes the epic before its
phase — `wave-plan.sh` DISPATCH, `fanout.sh` COLLECTED, `lens-gate.sh` LENSES,
`merge-wave.sh` GATE, `close-wave.sh` PUSHED — so a stall is diagnosable: "8.5 hours with
zero activity" tells you it stalled but not **where**, which is the difference between
diagnosing a permission prompt and an approval gate. They used to be five `tk.sh note`
lines per wave for you to remember, and were the most skippable lines in this file.

## 1–2. The epic queue, and the triage of each — one call

```bash
${CLAUDE_PLUGIN_ROOT}/harness/swarm/epic-queue.sh          # exit 0 at least one runnable epic · 1 none · 2 the tracker could not answer
```

| what it does | rule |
|---|---|
| every open epic, **P0 → P3, tie-break oldest** | `list --type epic --status open` |
| **gated → EXCLUDED, with the gate's reason** | a human gate blocks an issue from `ready` but **not** from `list --status open`, so a gated epic keeps reappearing here — the loop never terminates unless it is dropped explicitly. `tk.sh park` also leaves a `PARKED` note, which counts even where the backend cannot record the gate's target |
| **leased by another machine → EXCLUDED, naming the holder and the host** | claims and the merge slot are local to a checkout and no protection between machines: two campaigns can claim the same task, both merge, and the export conflicts on push or silently takes the last write. A lease *this* machine holds is a stopped run's own epic, not an exclusion. The remote unreachable is *said* — "leases NOT checked" — never read as free |
| each runnable epic **triaged** | `UNPLANNED` (no children) · `PARTIAL` (children, but none ready or none carrying acceptance criteria) · `READY` (ready children carrying criteria) — and its **dispatchable-on-entry count**, which is signal ⑤ |

An epic you parked earlier in *this* run is excluded for the rest of it — **never re-enter
an epic you have already parked.** Report the queue up front — ids, titles, priorities,
excluded-or-not with the reason, the triage state of each — so the owner can see the whole
run before it starts.

**The lease.** `MODE=auto` (`campaign.sh`) takes each epic's lease before its session and
releases it in a `finally`, whatever the session did. In `MODE=interactive`, take it
yourself before §3 — `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh lease acquire <epic-id>` exits non-zero if
held — and `close-epic.sh` releases it at §5. A crashed machine leaves its lease behind;
`lease steal <epic-id>` reclaims one past its TTL and refuses while it is still fresh, and
the steal is a ref update on the remote, never silent.

**Triage sets the DEPTH of the review, never whether it happens:**

| State | Architect is asked to | Planner is asked to |
|---|---|---|
| **UNPLANNED** | design it from scratch | decompose it into a DAG |
| **PARTIAL** | design it, reconciling whatever already exists | complete the DAG and write the missing criteria |
| **READY** | **sanity-check the existing design** | **revise the existing task plan** |

**This table measures PLAN COMPLETENESS, not whether the epic is specified well enough to
build.** Those are different axes and the architect answers the second one separately at §3b —
do not infer one from the other. An `UNPLANNED` epic can be richly specified and plannable
today; a `READY` one can be underspecified and will surface as lens FAILs three rounds deep,
which is the more expensive of the two. One campaign's ~36% first-pass rate came from a `READY`
epic, not an `UNPLANNED` one.

**Every epic goes through §3. There is no route that skips it.** An epic that looks ready is
the *most* dangerous one to dispatch unreviewed: its tasks were written before the current
gates existed, so they were never checked against the file-contention matrix, the megafile
width-1 rule, the decision-contention pass, or the standard that every acceptance criterion
must be something `verifier` can locate in a diff.

One recorded epic is the proof case. `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh validate` rated it **11-wide**; five of its six
wave-1 tasks touched the same ~1,900-line shared module, and two of them said **in their own
text** that they should be done together. Dispatching that queue as-is is a pile-up.

Report the state of each epic and what depth of review you are therefore running.

## THE LOOP IS SERIAL. ONE EPIC AT A TIME, ALL THE WAY THROUGH.

**§0, §1 and §2 are the only queue-wide steps.** They run once, over the whole queue, and
produce the report. **Everything from §3 onward is scoped to ONE epic**, and you do not start
§3 on a second epic until the first is **closed (§5)** or **parked** (gate + `--status
blocked`).

The order for each epic is: **3a → 3b → 3c → 3d → 3e → 3f → §4 waves → §5 close-out → §6
report → next epic.**

**Do not fan §3 out across the queue.** Dispatching eight analysts, then eight planners, then
eight audits is faster on paper and is the wrong shape. It has been tried and it fails the same
way every time:

- **Nothing lands.** A campaign that has surveyed eight epics and dispatched zero workers has
  produced no code, and the queue's real state — *are these tasks buildable?* — is still
  unmeasured, because only a wave measures it.
- **The findings arrive too late to change the plan.** §3d exists to catch a defect *before*
  dispatch. Eight plans in flight means eight sets of findings arriving after every plan is
  written, so the corrections cannot inform each other.
- **The orchestrator's context fills with epics it is not working on.** Each survey and plan is
  thousands of words. Carrying seven epics' worth while trying to run one epic's wave is how an
  orchestrator loses the thread of the epic actually in flight.
- **Parallel planning cannot see cross-epic contention it creates.** Two plans drafted
  simultaneously allocated the same decision-record number, and neither planner could see the other.

**The one thing that IS allowed to overlap** is §4's wave dispatch — the `n` workers of a
single wave go out in one message, which is what `/swarm` step 5 requires. That is parallelism
*within* an epic, which is the design. Parallelism *across* epics is not.

**If an epic parks at 3a (`ABSENT`) or at 3d (second FAIL), move to the next epic
immediately** — that is the loop working, not a failure, and it is the only time you leave an
epic before §5.

---

## 3. Design and plan — THE EPIC YOU ARE CURRENTLY ON

**"Dispatch" in this section means `dispatch.sh`, never the Agent tool** — for the survey,
the architect, the planner, the audit and the spec-editor exactly as for a wave's writers:

```bash
# prompt to a file, then a BACKGROUND Bash call, then read the DIGEST it prints
${CLAUDE_PLUGIN_ROOT}/harness/models/dispatch.sh analyst-survey --prompt-file <path> --task <epic> --digest
${CLAUDE_PLUGIN_ROOT}/harness/models/dispatch.sh architect      --prompt-file <path> --task <epic> --digest
${CLAUDE_PLUGIN_ROOT}/harness/models/dispatch.sh planner        --prompt-file <path> --task <epic> --digest
${CLAUDE_PLUGIN_ROOT}/harness/models/dispatch.sh analyst        --prompt-file <path> --task <epic> --digest
${CLAUDE_PLUGIN_ROOT}/harness/models/dispatch.sh spec-editor    --prompt-file <path> --task <epic> --digest
```

**`--digest` is how an artefact stays out of your context.** Every dispatch writes the
agent's whole result to a file under `.harness/run/out/` and, with `--digest`, prints only
its first lines and the path. The verdict line every agent puts first is what you read; the
body goes where it is consumed — the design to the epic note by path (§3b), the plan to
`apply-plan.sh` (§3e), the audit's findings to the planner's next prompt by path (§3d).
Measured: the four largest things injected into one campaign orchestrator's context were
subagent results of 45k, 43k, 36k and 23k characters, each paid on every later turn.

Measured, five campaign sessions: plugin agents spawned through the Agent tool read 67.2M
prompt tokens; through the dispatcher, 6.9M. Eighteen of ~22 architect, planner and
analyst runs took the Agent-tool path — with no `--max-budget-usd` ceiling, no tier, no
sandbox, no cost record, and none of the measured levers. This section said "dispatch"
without saying how, and that is how it drifted. A `PreToolUse` hook now refuses the Agent
tool for any of this plugin's agents and prints the form above, so the rule holds
without you remembering it. Read-only agents need no `--worker`; they run in the primary
checkout and write nothing.

### 3a. Adequacy — is this epic specified well enough to design against? (analyst-survey)

**Before the architect runs at all.** Dispatch **`analyst-survey`** on the epic. That is a separate agent from `analyst`, pinned at `effort: high` rather than `xhigh`, because the survey is a breadth-first corpus sweep and the audit at §3d is the sharper task.

**Why this and not the architect's own judgement.** The architect would otherwise decide *"do I
have enough to design?"* and then design — self-certification, the thing this pipeline refuses
everywhere else. And the cost of getting it wrong is asymmetric: an architect that wrongly
believes an epic is specified produces a design built on **invented scope**, which `MODE=auto`
then auto-accepts, and which every worker follows for the rest of the epic. Catching that after
the design is wasted design; catching it after the plan is wasted planning.

The analyst returns the adequacy verdict **and** everything that already constrains the answer —
the adjacent feature docs, the binding decision records, the settled owner decisions (including
closed ones), what the information architecture and NFRs fix, and what exists in code.

| Verdict | Action |
|---|---|
| `ADEQUATE` | proceed to 3b |
| `INFERABLE` | proceed, and **record every inference as an unchecked acceptance criterion** in the owning feature doc at fold-in ① below (not only in the epic's `ARCHITECTURE:` note, which disappears when the epic closes). An inference the owner never sees is an invention with better manners; one that vanishes at epic close is an invention with an alibi. |
| `ABSENT` | **do not run the architect.** Draft first, then re-judge — see *The ABSENT path* below. Only a draft that still fails the audit reaches the owner. |

**This is not overhead.** Hand the SURVEY output to **both** the architect and the planner. It
is the corpus sweep they would each otherwise do — separately, partially, and under context
pressure — so the dispatch mostly pays for itself in work they no longer repeat, and the two
of them start from the same set of facts rather than two different partial ones.

#### The ABSENT path — draft from the corpus before spending the owner

**Never send a blank `ABSENT` straight to `/requirements`.** That contradicts the survey's own
reason for existing: *no question is asked whose answer the repo already holds*. The verdict is
returned **per epic**, but the answers are **per criterion** — where the corpus supports four
criteria and not two, sending it onward asks the owner about all six.

1. **Dispatch `spec-editor` to draft the epic's proposal** under `paths.proposed` from the
   SURVEY's findings, `status: draft`. **Every drafted criterion carries the doc and the quoted
   opening words it derives from.** A criterion that cannot be cited **may not be written** — it
   goes under `## Open questions` instead. This is assembly from what the owner already approved,
   never invention, and it is why the drafter is `spec-editor` (forbidden to invent scope or
   improve requirements) rather than an analyst.
2. **Dispatch `analyst` in AUDIT mode on the draft.** It did not write it, so this is not
   self-certification.
3. **`ADEQUATE`** → proceed to fold-in ①. Record `CORPUS-DERIVED — no owner input` on the epic,
   so a spec assembled without a human saying anything is **visible** rather than silently
   indistinguishable from one the owner dictated.
   **Otherwise** → file the `REQUIREMENT:` task, `park` the epic, and send it to
   `/requirements` — **with the partial draft**, so the conversation starts at *"here are the four
   we derived, confirm them; these two we cannot answer"* rather than at zero.

> **The risk this buys, and the guard on it.** A pre-filled draft anchors the owner into
> reviewing rather than stating, and the point of `/requirements` is their actual intent. The
> citation rule is the whole defence: the owner is reading back what the corpus already says,
> attributable clause by clause. An uncited criterion in a draft is a defect, not a shortcut.

#### Fold-in ① — apply the epic's proposal to the corpus, before the architect runs

On `ADEQUATE` or `INFERABLE`, the epic's staged proposal is applied to the corpus **now**,
before `architect` is dispatched. See the `spec-lifecycle` skill.

**Dispatch `spec-editor`**, not the analyst. Hand it the proposal and the SURVEY's SPEC INDEX —
that index already names every doc that governs this epic, which is exactly the edit list. It
writes the proposal's acceptance criteria into the owning feature doc as
**unchecked** criteria, plus any `## Behaviour` / `## Data` / `## API` prose they require, and
then marks the proposal **`folded in`** with the date.

**It is not deleted here.** After fold-in the feature doc shows the merged end-state and nothing
else states this epic's **delta** — and "unchecked criteria" is not a proxy for it, because a doc
can carry unchecked criteria an earlier epic left behind. The architect at §3b and the planner at
§3c both run *after* this point and both need to know what *this* epic changes. The whole folder
is deleted at §5.

**Why not the analyst.** Both analysts grant `tools: Read, Grep, Glob, Bash` — no `Edit`, no
`Write` — and their mirrored guardrail is enforced by `${CLAUDE_PLUGIN_ROOT}/harness/checks/check-analyst-mirror.sh`. The
structural reason is the smaller one. The real reason is that the analyst's value is judging a
spec **it did not write**: let it apply the proposal and at §3d it audits a plan built on a
corpus it authored. That is the self-certification this stage exists to refuse.

**No proposal file** — say so and proceed. Do not synthesise one.

Most open epics have none, and for the majority that is **correct rather than degraded**. They
are gap epics — *"the decision record for X is entirely unimplemented"*, *"the documented
background jobs mostly do not exist"* — and their requirements already live in the corpus. That
is what makes them gap epics. One measured corpus found roughly three quarters of feature docs
describing built code, the rest partial or unbuilt. There is nothing to fold in because it is
already in.

Such an epic still gets a folder — the spec index lands there at §3a and the design at §3b. Only
`proposal.md` is absent, and it appears **lazily**, the moment `analyst-survey` returns `ABSENT`:
that path already files a `REQUIREMENT:` task, parks the epic, and routes to `/requirements`,
which writes the proposal. No bulk backfill is needed or wanted.

**But the decision gate does not run without a register**, and in one measured backlog all but
one open epic had none.
`${CLAUDE_PLUGIN_ROOT}/harness/checks/check-decision-register.sh <epic-id>` now says so out loud rather than exiting silently
— silence there reads as *checked and clean* when it means *never checked*. When it reports no
register, **check by hand**: `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh list --type=decision --status=open`, and park the epic on any
open decision that binds it. An open decision blocks whether or not anyone recorded the
association; the register only makes it mechanical.

**The decision register gates fold-in.** Run `${CLAUDE_PLUGIN_ROOT}/harness/checks/check-decision-register.sh <epic-id>`.
It fails when a row disagrees with the tracker, when a settled row records no resolution, when a draft
ADR has no row, or when a row cites a file that does not exist. **Any row still in the register's Open table parks the epic** — that
is what an unresolved decision means, and folding in over one writes a criterion the owner
never settled. Its **Settled** table is free context for the architect: a spec written against
a settled answer costs nothing, one written against its opposite is rework.

**Contention across staged proposals.** The SURVEY reports any other open staged proposal
whose frontmatter `lands_in` overlaps this epic's. `${CLAUDE_PLUGIN_ROOT}/harness/checks/check-decision-register.sh` reports
it in the same run that verifies the register — it reads YAML, so a doc path mentioned in prose
no longer false-positives, and a proposal already marked `status: folded-in` is skipped because
it is applied, not pending. Treat a real clash exactly like two accepted ADRs disagreeing:
surface it, name which one lands first, and gate rather than folding both in and discovering the
contradiction in the corpus. It is cheap because proposals are short-lived — fold-in at epic **start** means few
coexist, and a long queue of open proposals is itself the signal that specification is running
too far ahead of the build.

**Why at the start and not at the end.** Fold-in at epic close means the spec is written by
whoever just built the thing, and it faithfully documents the shortcut. Folding in first keeps
the spec an *independent target* the architect, planner and verifiers can be judged against —
and bounds how far the corpus can lead the code to one epic rather than a roadmap.

**Judge the text, never the child count.** Plan completeness and specification adequacy are
orthogonal: an `UNPLANNED` epic can be richly specified and plannable today, and a `READY` one
can be underspecified and will surface as lens FAILs three rounds deep. This run's ~36%
first-pass rate came from a `READY` epic.

**Record the verdict AND the spec index before proceeding.** The verdict's absence is what a
later run reads as "nobody checked"; the index is the map every downstream agent needs and
would otherwise re-derive:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh update <epic> --append-notes "ADEQUACY: ADEQUATE|INFERABLE|ABSENT <date> — <inferences, or 'clean'>

SPEC INDEX (regenerated each run — a cache, never a source):
  SPEC:      <the owning feature doc> — sections that govern: <named>
  DECISIONS: <record id> §N (<what it binds>) · ...
  DECISIONS: D-x (<verbatim one-line answer>) · ...
  PRODUCT:   <IA / NFR constraints that are fixed>
  CODE:      <what exists, by symbol>"
```

**Index the specs — never harvest them.** Record *pointers*, not content.

Copying spec prose into the epic manufactures this repo's most expensive defect class: **two
accepted documents disagreeing.** The feature folder under `paths.features` owns the schema,
endpoints, screens and acceptance criteria; a harvested copy becomes a competing source of
truth that drifts the moment either side is edited, and drifts *silently* — stale prose reads
fine and is wrong. A stale **pointer**, by contrast, is a grep away: if the index says
a decision record and section, and that section no longer exists, anyone can see it.

It is also the practical choice. Feature docs run to hundreds of lines, and a task's record has
a **~64KB ceiling** past which `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh note` hard-fails — one task hit it after ten rounds of lens
reports. Harvesting would reach that
wall on epics that have not started.

**Regenerate the index on CHANGE, not on schedule.** "Regenerated each run" is the rule for
*staleness*, not a licence to rebuild an index nothing has invalidated. A survey costs ~120k
tokens and forty-odd tool calls, almost all of it corpus exploration.

Before dispatching `analyst-survey`, check whether the epic already carries an
`ADEQUACY:` note with a SPEC INDEX:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh show <epic> | grep -A25 'ADEQUACY:'
git log --oneline --since=<index date> -- <the docs and code paths the index names>
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh list --parent <epic> --json    # have children been added, closed or re-scoped?
```

**Run `${CLAUDE_PLUGIN_ROOT}/harness/checks/spec-index-status.sh <epic-id>` — it answers this table.** The index records the
SHA it was generated against and every doc it cites, so "has anything it cites moved?" is a diff,
not a judgement. It prints `REUSE`, `DELTA` with the changed paths, or `REBUILD` — and either id
form, bare or prefixed, is accepted. A `REBUILD` that says *no staging folder* and names the
patterns it tried is a lookup miss, not a verdict: check the folder name before surveying.

| Finding | Action |
|---|---|
| No index, or the epic's children have changed | **Full SURVEY.** |
| Index exists and nothing it cites has moved | **Reuse it.** Say so in the report, with the index's date and what you checked. Do not re-dispatch. |
| Index exists but some cited docs have moved | **Dispatch a DELTA survey** — hand the analyst the existing index and the list of changed paths, and ask it to verify and extend rather than rebuild. This is the common case and it is a fraction of the cost. |

**A DELTA closes its own loop, or it re-fires forever.** The survey verifies the changed docs
and writes its findings to the epic; nothing about that moves the index's `generated_sha`, so
the next run diffs against the same old baseline, finds the same paths moved, and dispatches
the same survey — an epic that DELTAs once pays a survey every run. When the DELTA survey has
landed, **you** move the baseline (the analyst will not infer it, and an interrupted survey
must not stamp what it did not check):

```bash
${CLAUDE_PLUGIN_ROOT}/harness/checks/spec-index-status.sh <epic-id> --stamp [--cite <path>]...   # baseline → HEAD, today; cites extended
```

**The adequacy VERDICT still has to be current** — if the epic's children changed, the verdict
is stale even when the corpus has not, because adequacy is judged against what is being built.

**Effort IS tiered between the two, and the split is deliberate.** `analyst-survey` runs on
the `worker` tier (`effort: high`); `analyst` (AUDIT, §3d) stays on `strong` (`xhigh`) because
it has caught a false premise in every plan it has read. The tier is one value per agent
file (`model_tier:`), resolved by the dispatcher against `tiers.yaml`, so two files are the
only way to tier them.

**The cost of that split is drift**, and it is contained rather than hoped away. The two files
share a 42-line block of *guardrails* — "you never write requirements, and you never enhance
them" — which must be present unconditionally, so it is duplicated rather than factored into a
skill an agent might fail to load. Both copies are wrapped in `MIRRORED BLOCK` markers and
`${CLAUDE_PLUGIN_ROOT}/harness/checks/check-analyst-mirror.sh` fails loudly if they diverge, and fails again if the two
efforts are ever set equal (which would make the split pointless) or if the AUDIT is ever
lowered off `xhigh`. **Run it after touching either agent file.**

**Where it lives.** Under `paths.proposed`, from that directory's spec-index template, with
`generated_sha` and `cites` in frontmatter — that
is what makes the reuse decision mechanical. Persist a pointer on the epic, not the map itself.

**The index is a regenerated cache, not a maintained artefact.** The analyst rebuilds it every
run, so staleness self-heals and nobody owes it upkeep. That is what makes it safe to keep
close to the work — and it is why it must never be the thing anyone *reads the requirement
from*. It tells you where to look; the doc tells you what is true.

### 3b. Architect

**UNPLANNED / PARTIAL — design.** Dispatch `architect` if any architecture-gate test trips:
a new or changed data model or migration; a new rule in a core domain engine; a new adapter
behind an existing extension point; a new boundary between bounded contexts or a new shared
service; a new API resource or a changed response shape; or more than ~5 tasks expected. Otherwise state which test failed and
skip to 3c.

**READY — sanity-check.** Dispatch `architect` with the epic, its existing tasks, and any
`ARCHITECTURE:` note, and ask it to answer three questions:

1. **Is the recorded or implied design still correct** given everything that has landed since
   the tasks were written? Check the feature docs and the ADRs — including ones written after
   these tasks.
2. **Has the ground moved underneath it?** The class to look for: a recorded memory finding
   that some documented framework is *a veneer whose protocol methods are called nowhere* — an
   epic planned against the documented contract would be planned against something that does
   not exist. Run `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh memories` and check for exactly this drift.
3. **Confirm, or flag the drift precisely.** A sanity-check that returns "looks fine" without
   naming what it checked is not a sanity-check.

Record the outcome either way — a confirmation is worth as much as a correction to the next
run:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh update <epic> --append-notes-file <the architect's --digest output file>
# or, for a sanity check with nothing to attach:
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh update <epic> --append-notes "ARCHITECTURE: SANITY-CHECKED <date>: <what was checked, confirmed or corrected>"
```

**By file, never by retyping it.** The architect's output already begins with its
`ARCHITECTURE:` block; attaching the file costs you nothing, where reading it in and
emitting it again as a note paid for the design twice at your context size. Use
`--append-notes` / `--append-notes-file`, **never `--design`** — that field is write-only
and invisible to `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh show`.

**Before the design, take the architect's SPECIFICATION-ADEQUACY VERDICT** — `ADEQUATE`,
`INFERABLE` or `ABSENT`. It comes first in its output and it changes what you do next:

| Verdict | Action |
|---|---|
| `ADEQUATE` | proceed to the design gate as normal |
| `INFERABLE` | proceed, and **record every inference it listed** in the `ARCHITECTURE:` note. An inference the owner never sees is an invention with better manners. |
| `ABSENT` | **park the epic. Do not design, do not plan, do not dispatch.** |

**`ABSENT` is a parking condition in both modes, and in `auto` it is the sharper rule:**
auto-accept covers **design**, never **invented scope**. An epic with no children and no
acceptance criteria will otherwise be designed by an auto-accepted architect, and
`/campaign-auto` is explicit that the architecture you accept is what every worker follows for
the rest of the epic — so an absent specification silently becomes an invented one, with no
signal to the owner that scope was invented rather than specified.

The architect files a **requirement record** (`REQUIREMENT:` prefixed, type `decision`, so it
inherits this section's gate machinery). You park exactly as for a decision:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh park <epic-id> --reason "REQUIREMENT owed: <one line>"
```

**Un-parking a requirement gap is `/requirements`**, not `/decision`. A decision is one
question with options; a requirement gap is a conversation that ends in a feature doc. Say so
in the report — the owner should know which command to reach for, and `/decision` on a
requirement record will produce a fork where none exists.

**A requirement gap is not a decision, and the report must not blur them.** A decision is a
fork the owner picks between; a requirement gap is an absence the owner must fill. Count and
list them **separately** in the final report — "3 epics gated on decisions, 2 on missing
requirements" tells the owner two different things about their backlog, and the second is the
one that predicts the next run's first-pass rate.

**Why this is worth a gate.** In one campaign the first-pass lens PASS rate ran at ~36%, under
the floor at which this document says *the tasks are underspecified — fix the planner,
never the worker*. Every failure was a real defect and most traced to something the task never
said. Underspecification surfaced as lens FAILs three rounds deep. This moves that discovery to
before dispatch, where it costs one question instead of three remediation rounds.

**Gate — `MODE=interactive`:** follow **§3g Approval precedence** — render it verbatim and
`AskUserQuestion` (accept / revise / reject) **only when the whole queue is dry**; otherwise
queue it and move on.
**Gate — `MODE=auto`:** accept, record `AUTO-ACCEPTED`, flag it in the epic report. Any open
question from the architect is a `decision` task and parks the epic.

#### Stage the design — the proposed directory, not the corpus, not only a task note

Write the accepted design to **`<paths.proposed>/<epic-id>-<slug>/design.md`** from that
directory's design template, and open a **draft decision record** alongside it for each
`decision` task the architect raised.

It does not go into the corpus yet. A point-in-time design is not a description of the system,
and it cannot fold in at §3a because it did not exist then. It folds in at **§5, fold-in ②**,
routed by content — the *why* to a decision record, the *mechanism* to an architecture doc, a
changed contract to the owning feature doc — and is then deleted.

**Recording it only in the epic's `ARCHITECTURE:` note is the failure this replaces.** That note
disappears when the epic closes, taking the reasoning every later reader needs with it. A
separate "plans" directory fails the same way: such files drift into carrying an *"where this
disagrees with as-shipped, as-shipped wins"* disclaimer and stop being reachable from the
corpus index. Keep the `ARCHITECTURE:` note as the tracker's pointer; the file carries the
content.

**Nothing may cite a draft decision record as settled** — not the planner, not a worker, not
another record.
It has no number until the owner decides.

### 3c. Planner

Dispatch `planner` with the epic, its **current** tasks, the `ARCHITECTURE:` note, the lane
vocabulary and caps, and the instruction to produce a **file-contention matrix** and a
**decision-contention pass**.

**UNPLANNED / PARTIAL** — produce the DAG, or complete it.

**READY — produce a REVISION PLAN.** The planner audits the existing tasks against the
standards they were never checked against, and **may add, delete or modify tasks as needed to
make the plan well constructed.** Audit each task for:

- **Slicing** — one task = one commit = one reviewable change = **one acceptance criterion a
  verifier can check without reading the plan.** Needs two commits? Split it. Two tasks that
  are really one change? Merge them — tasks sometimes say so in their own text.
- **Acceptance criteria** — present, concrete, and **locatable in a diff**. "Works correctly"
  is not a criterion; `verifier` FAILs anything it cannot point at a file and line for.
- **File contention** across the ready set, including the **megafile width-1 rule** — any file
  past `signals.megafile_lines` may be touched by at most one task per wave.
- **Decision contention** — no two tasks able to answer the same open question differently.
- **Lane labels** — correct and present, or the task is unroutable.
- **Dependency edges** — the data layer before the UI that consumes it; any API-shape change
  followed by a client-type regeneration task.
- **Staleness** — tasks superseded by work that has since landed, or written against a design
  the architect just corrected.

Output a **revision plan**: for each task, one of `keep` / `merge into <id>` / `split into N`
/ `re-scope` / `re-label` / `add-dep <id>` / `supersede` / `delete`, **each with its reason**.
Say `keep` explicitly for tasks that pass — a revision plan that only lists changes hides how
much was reviewed.

**Rules on destructive edits** (the planner proposes; `apply-plan.sh` executes — see 3e):

- **Never touch a task that is `in_progress` or `closed`.** Someone may be working it right
  now. Re-scoping around it is fine; editing it is not.
- **Prefer `supersede` over `delete`** where there is history worth keeping — a task other
  work references, or one carrying analysis in its notes. Use `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh supersede <old> --with <new>` (it closes the old one with a reference to the replacement).
- **`delete` is for tasks that are simply wrong or obsolete** with nothing worth preserving.
- **Re-point dependencies before removing anything**, or you strand its dependents.
- **Never delete an epic.**

### 3d. Audit the plan before you approve it (analyst) — this epic, both modes

**Every plan goes through this audit. There is no route that skips it**, exactly as §3 admits
no route around design. **You may not approve a plan you have no audit verdict for** — the
verdict is a precondition of the gate below, not a step alongside it.

**Dispatch `analyst` in AUDIT mode on the planner's output** — the task set, its acceptance
criteria and its `SURFACE:` lines — **before** the gate below. Read-only, no execution, so it
sits in the cap-8 lens class and costs one dispatch per epic.

**Why here and not at 3b.** The architect's specification-adequacy verdict is a *self*
assessment: it judges whether it has enough to design, then designs. Nothing checks that
judgement — the same self-certification this pipeline refuses everywhere else. And it is the
planner's output, not the architect's design, that workers actually build from: **the
acceptance criteria on a task are what `verifier` later checks against.** Audit the artefact
that ships.

**This is the expensive case, not the obvious one.** An `UNPLANNED` epic at least announces
that it needs design. The costly shape is a `READY` epic whose tasks *exist* and dispatch
clean, and whose thinness only surfaces as lens FAILs three rounds deep. That is exactly what
happened at ~36% first-pass: eleven children, all dispatchable, none saying which ADR or owner
decision its surface touched.

The analyst judges each task against the same standard it applies to a spec — criteria
locatable in a diff, behavioural rather than implementational, failure and empty and permission
states present, every actor named, no ambiguous quantifier, terms against the glossary, no
contradiction with an accepted decision record, the invariant surface stated, the scope
boundary written.

**Route the verdict:**

| Outcome | Action |
|---|---|
| PASS | proceed to the gate below |
| FAIL, first time | **dispatch `planner` afresh with the audit's output file path in its prompt** and re-audit. This is a revision, not a re-plan — but it is a NEW dispatch, never a resumed one: measured, resuming a lens rebuilt its whole prior conversation at the cache-write rate ($1.95 for two round-trips against $0.16), because a resumed prompt does not match the cached prefix. A fresh planner pays one clean prefix and reads the findings from the file. |
| FAIL, second time | **the epic is underspecified at the epic level.** File a `REQUIREMENT:` task and `park` the epic. |

**That second failure is the loop closing.** A plan that cannot be made dispatchable in two
passes is not a planning problem — it is an absent specification wearing a DAG. It goes to
`/requirements` with the owner, which is the only thing that can actually fix it, and the
analyst's findings are the agenda for that conversation.

**The governor is the analyst's own, and it matters here.** A plan with gaps **written down**
— as `## Open questions`, as a `decision` task, as a stated deferral on the task — is a PASS.
A plan with the same gaps **silent** is a FAIL, because silence is what gets built over. Do not
let this become a completeness ritual that blocks every real plan.

**`MODE=auto` does not skip it.** Self-approving a plan is exactly when an independent read of
its quality is worth most — the whole risk that mode carries is that the architecture and the
DAG you accept are what every worker then follows.

**Gate — `MODE=interactive`:** same precedence (§3g) — queue it unless the whole queue is dry.
When you do ask, render the DAG, the contention matrix and the revision plan **verbatim**,
together with 3a's design in the *same* question, surfacing `decision` tasks first and calling
out deletions explicitly.
**Gate — `MODE=auto`:** approve and apply, unless the plan leaves an unresolved contention
edge or surfaces a `decision` task — either parks the epic.

**Record the verdict on the epic before you apply anything** — it is a required field, and its
absence is what a later run reads as "this plan was never audited":

```bash
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh update <epic> --append-notes "AUDIT: PASS|FAIL <date> — <findings, or 'clean'>"
```

### 3e. Apply the plan — one call

```bash
${CLAUDE_PLUGIN_ROOT}/harness/swarm/apply-plan.sh <the planner's --digest output file> --epic <epic> --dry-run   # the preview
${CLAUDE_PLUGIN_ROOT}/harness/swarm/apply-plan.sh <the planner's --digest output file> --epic <epic> \
    --render <paths.proposed>/<epic>-<slug>/tasks.md                                                   # the apply
```

The planner's command block carries labels (`T1: … create …`, `dep T2 T1`); the script
validates the whole plan first — an unknown label, a `--graph`, a positional `close` — and
writes nothing if any line is wrong, then runs the lines in order, resolves every label to
the id the tracker returned, echoes each, and records the map under `.harness/run/` so a
rerun after a failure skips what already exists. It ends with `validate` and the render
(3f), so one call replaces the ten to thirty you made before, each at your context's price.
Never `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh create --graph`: `--dry-run` is silently ignored on that path, so a
malformed plan writes real tasks with no preview — the script refuses it.

**Decision-record numbers are allocated BEFORE the planner runs, not while applying** — one
`ls <paths.adrs>` gives the next free number; put it in the planner's prompt so its plan
carries it. Never leave a worker to pick one; two streams picking independently have already
collided that way.

### 3f. Validate — done by the apply

`apply-plan.sh` runs `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh validate <epic>` and, with `--render`, regenerates the
epic's readable view (`tasks.md` in the staging folder — GENERATED, regenerate it, never edit
it). Read its last lines: no cycles or orphans, the waves and max parallelism — **and restate
that the number ignores file contention.** That is precisely how an epic reads as 11-wide
when its file graph supports about two.

## 3g. Approval precedence — never block while work remains

**`AskUserQuestion` suspends the orchestrator.** It is a blocking call: while it is pending
you are not running and *cannot* look for other work. A timeout cannot rescue you — by the
time it fires the time is spent. The only fix is to **not ask while anything is dispatchable.**

| Order | Condition | Action |
|---|---|---|
| 1 | This epic has ready tasks | **Dispatch them.** No prompt. |
| 2 | This epic is dry, but **any other epic** has work | **End at the epic boundary** (below): `/clear`, then `/campaign` picks it up. An invocation is one epic. |
| 3 | Nothing dispatchable **anywhere in the queue** | **Now** ask — *one* batched question covering every queued approval. |
| 4 | Step 3 unanswered after **30 minutes** | `park` the queued epics and end the run cleanly. |

**The trigger is the whole queue running dry, not this epic running dry.** Asking one epic
early suspends the orchestrator while other epics still have ready tasks — the same stall,
just later in the run.

**Queue an approval like this.** The architect and planner still run; only the *asking* moves:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh update <epic> --append-notes "ARCHITECTURE (AWAITING APPROVAL): <design>"
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh update <epic> --append-notes "PLAN (AWAITING APPROVAL): <DAG + contention matrix + revision plan>"
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh label add <epic> awaiting-approval
```

**Nothing is applied and nothing is built from an unapproved plan** — no `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh create`, no
`${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh delete`, no dispatch. When you do ask at step 3, the owner sees every pending design and
DAG together, which reviews better than the same content as eight interruptions.

With a healthy queue — several open epics and a lane with tasks ready — **step 3 should rarely
be reached.**

`MODE=auto` never enters this precedence — it self-approves at 3b/3c. Its parking conditions
remain the `decision`-task hard line and the circuit breakers.

## 4. Wave loop — until this epic has no ready children

Repeat, up to **`MAX_WAVES = 6`** per epic:

1. **Pick the dominant lane** from `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh ready --parent <epic> --json`, then
   compose the wave with `${CLAUDE_PLUGIN_ROOT}/harness/swarm/wave-plan.sh <lane> --parent <epic>` — it clamps
   `n` to the lane cap and the global cap, asks every candidate's resume point, drops shared
   paths and megafiles, and opens the epic's wave manifest (`<epic>-w<k>`) that every wave
   script below writes to. `/swarm`'s per-class table is authoritative for agent classes and
   caps `verifier-tests` and `fidelity-auditor` at 2 regardless of the lane.
2. **Run the `/swarm` procedure, steps 2–9**, scoped to this epic's ready queue: contention
   re-check (including the megafile width-1 rule and the shared-vocabulary check), dispatch
   all `n` in a single message, collect, **the verification gate** — one call per `PASS`
   claim, `${CLAUDE_PLUGIN_ROOT}/harness/swarm/lens-gate.sh <id> <sha> --branch <b> --lane <lane> --worker <n> --wave <epic>-w<k>`:
   the brief once, the L4 trigger computed (never from the worker's summary), the suite
   once where the change is, L1–L3 and L4-on-trigger at once with L3 handed no diff path,
   unanimity, the `VERIFIED` note on all-PASS and **nothing** on a FAIL or a missing verdict
   (`/swarm` step 7 has the table) — then integrate + whole-repo wave gate, **the wave-stage
   `/code-review` and accretion check (`/swarm` step 8b)**, tasks sync.

   **Every writer's task is asked `${CLAUDE_PLUGIN_ROOT}/harness/swarm/resume-point.sh <id>` first** — `/swarm`
   step 5's table. A run that was stopped mid-wave left branches; MERGE and VERIFY need no
   worker, REATTACH dispatches with `--resume <branch>`. Only FRESH dispatches from scratch.
   Skipping this is how one task grew five branches.

   **Writers get `--worker <n>` on `${CLAUDE_PLUGIN_ROOT}/harness/models/dispatch.sh`, which creates AND populates
   the worktree (`claude -p` does not honour `isolation: worktree` itself); the read-only lenses
   do not.** A fresh worktree has none of the gitignored dependency directories; `--worker <n>`
   runs `${CLAUDE_PLUGIN_ROOT}/harness/swarm/swarm-worktree-init.sh` for you before the agent starts, so the worker
   opens with its dependencies restored and its own isolated per-worker resources. Omit `--worker` and the dispatcher
   REFUSES rather than running a writer in the primary checkout.
   - `MODE=interactive` asks `/swarm`'s step-4 confirmation **only when step 3 changed the
     wave** (a task dropped for contention, a megafile collision, a `SKIPPED already claimed`).
     If the wave is an unmodified subset of the DAG already approved at §3c, state it and
     dispatch — re-confirming a no-op trains the owner to approve without reading.
   - `MODE=auto` skips it — the plan was already approved at 3c.
3. **Close the wave's tasks and sync — one call, WITHOUT `--restore-autosync`:**

   ```bash
   ${CLAUDE_PLUGIN_ROOT}/harness/swarm/close-wave.sh <id>="<what shipped, how verified>" <id>="…"     # no --restore-autosync inside a campaign
   ```

   That is `/swarm` step 9 — the closes, `export`, the epic's view regenerated (its
   `--check` runs first, so a view somebody hand-edited is said on the line before it is
   overwritten: "generated, so do not edit it" is only a claim until something enforces
   it), the commit, `pull --rebase --autostash`, the push, `git status -sb` up to date.
   **`--restore-autosync` is omitted here and passed by a standalone `/swarm`**: this run
   owns the tracker until §5, and re-enabling the backend's own export between waves would
   have it staging `issues.jsonl` into the next wave's worker commits — the thing §0
   turned it off to prevent. If the rebase pulled in another actor's commits the call
   stops before the push; re-run the wave gate on the rebased tree, then
   `close-wave.sh --sync-only`.

4. **The push is the wave's terminal action** and the call above confirmed it; an
   unattended run must never strand work locally.

   **Then reclaim the wave's worktrees:** `${CLAUDE_PLUGIN_ROOT}/harness/swarm/worktree-sweep.sh --apply`. It skips any
   worktree touched in the last 30 minutes, so a concurrent agent's tree is never pulled out
   from under it. Each dispatched
   task leaves one, and they are worthless once the branch is merged but *actively dangerous*
   once stale — a worktree for a task under remediation was found holding a **staged revert**
   of the fix, while `git log` on the branch still showed the good commit. Sweeping after every
   wave keeps the count near zero, so the one that is left is always the one that means
   something. It never touches uncommitted work.
5. **Circuit breakers — one call after every wave:**

   ```bash
   ${CLAUDE_PLUGIN_ROOT}/harness/swarm/breakers.sh <epic>        # exit 0 clear · 1 tripped, your call · 2 hard park · 3 stop the run
   ```

   Every trip is a count over the wave manifests — which is why they are on disk: until
   0.10.22 these counters lived only in your context, which a compaction loses (a summary
   kept 2 of 9 task ids), and the recorded consequence was overriding the third-round breaker
   three times across three tasks in one run. The script says **which** tripped and quotes
   the action; **the action is yours** to take, or to override — saying in the report that
   you did, why, and that it is an override.

   | Trip | Action |
   |---|---|
   | Wave gate red twice in a row | gate the culprit task (the gate attributed it), then **re-check** (below) |
   | The same task FAILs the lenses twice | gate that task (not the epic), continue |
   | **A task enters a THIRD lens round** | **SPLIT it, do not remediate again** — see below |
   | A `decision` task appears | gate that task — the hard line — then **re-check** |
   | `MAX_WAVES` reached | **`park` the epic**, reason "needs another campaign run" |
   | Zero tasks closed in a wave | gate what blocked, then **re-check**; park only if it comes back empty |
   | **Three epics parked consecutively** | **stop the whole run** — something systemic is wrong |

   **The third-round breaker — split, do not stop.** This is deliberately a *different*
   prescription from the others. Two failing rounds means the task was underspecified; **three
   means the task is too big**, and a fourth remediation buys another round of the same.

   The live proof: one task absorbed **four** rounds while spanning an anchor derivation, a
   monotonic floor, an API input bound, a floor cap and doc amendments across four files. Each
   round closed its findings honestly and each *fix added new surface for the next lens to
   find* — that is not churn, it is a task doing four tasks' work. It also grew its tracker record
   past a **~64KB event-column ceiling**, after which `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh note` **hard-fails** — a 38-character
   append fails exactly as a 3KB one does, with no warning as you approach it.

   So when a task enters a third round:

   1. **Split it along the seams the lens rounds revealed** — they are usually obvious by then,
      because each round's findings cluster. `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh supersede <old> --with <new>` where there is
      history worth keeping.
   2. **Land the part that is already lens-clear**, if it is separable. Verified work sitting
      on a branch is the failure mode this campaign exists to avoid.
   3. **Only if it is genuinely one indivisible change**, gate it and say so explicitly — the
      owner is then choosing between funding a fourth round and accepting a stated residual.

   **Never override this breaker silently.** If you do override it, say in the report that you
   did, why, and that it is an override — and set a hard stop. Overriding it three times across
   three tasks in one run, as has happened, is a pattern the report must surface rather than
   bury.

   **Health-signal tie-in:** a third round is also signal ③ (slicing). Record it there, with the
   line count and the number of distinct concerns the task turned out to span.

   **The re-check — gate the task, not the epic.** Gating one task does not block its
   siblings: the tracker drops the gated task and its dependents from `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh ready` and leaves the rest.
   So after gating anything:

   ```bash
   ${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh ready --parent <epic> --limit 100 --json     # anything left?
   ```

   **If it is non-empty, compose the next wave from what remains and keep working this epic.**
   Park the epic only when this comes back empty. A live case: 2 tasks gated, **3 still ready**,
   17 open — the old rule would have abandoned an epic with three dispatchable tasks.

   `MAX_WAVES` and three-consecutive-parks stay hard parks: budget and systemic signals, not
   dependency ones.

## 5. Epic close-out

Two things are yours before the call, because they are judgement:

- **Tests and docs, confirmed — not assumed.** The wave gate was green on the final wave;
  `verifier-tests` and `verifier-spec` passed every task, which is what enforces that the
  tests are adversarial and that the feature doc, decision records and corpus index kept
  up. If the epic changed a contract and no doc changed, say why explicitly.
- **Fold-in ② — the design.** Dispatch `spec-editor` to apply the staged `design.md` and
  route it by content: a non-obvious choice becomes a **decision record**; a mechanism
  others will reuse edits the owning **architecture doc**; a changed contract edits the
  owning **feature doc**. Then the file is deleted. A **resolved** draft is `git mv`d into
  `paths.adrs` with `Status: Accepted` and `## Decision` filled in — it *moves* rather than
  merging, because a decision record is a standalone append-only file while a proposal is an
  edit into shared prose. An **unresolved** draft means its `DECISION:` task is still open:
  `park` the epic on it rather than closing over it.

**Then one call closes it:**

```bash
${CLAUDE_PLUGIN_ROOT}/harness/swarm/close-epic.sh <epic> --check                                   # every gate, nothing written
${CLAUDE_PLUGIN_ROOT}/harness/swarm/close-epic.sh <epic> --reason "<what shipped, how verified>"   # gates → render → archive → gates → close → sync
```

| step | what it is | on failure |
|---|---|---|
| `tk.sh list --parent <epic>` | **every child is closed or gated** — an epic does not close over a child `ready` would still offer under it | stop; the live children are named |
| fold-in ② done | no `design.md` survives in the staging folder — archiving a folder that still holds it is "silently discarded" with a stamp on it | stop |
| `render-epic.sh <epic> --write …/tasks.md` | **the view one last time, BEFORE retiring the folder** — the last wave's copy is stale the moment anything closed after it, and this is the version a reader finds a year later | stop |
| `archive-epic.sh <epic>` | **the single retirement point for everything the epic staged** — `proposal.md` and `decisions.md` included, MOVED to `paths.archive` dated and stamped `status: archived`. With no archive declared, deletion is the retirement and the next gate asks for it | stop |
| `check-blocking-prose.sh --strict` | **no task is blocked in prose only** — three landed in one session, each stating its blocker plainly in a note and each dispatchable anyway | stop, nothing written |
| `check-decision-register.sh <epic>` | the register is consistent **and its Open table is empty** — an epic does not close over an unresolved decision it raised | stop, nothing written |
| staging folder retired | absent or empty — **and, where an archive is declared, the archive entry exists.** Emptiness alone is not evidence of fold-in: a folder deleted without folding anything in is just as empty, so with an archive declared and no entry, git is asked whether anything was ever staged | stop, nothing written |
| `tk.sh close` · `tk.sh export` · commit · `pull --rebase --autostash` · push · `autosync on` · `lease release` | the close, then the shared sync tail (`/swarm` step 9's table). The archived folder — its `git mv` and the stamps — commits **with** the export, so the tree is clean after; `autosync on` restores what §0 disabled and nothing else does; the epic's lease is released if this machine held one | stop at the failing step; the rest listed by hand |

Twelve calls at your context's price, in an order the loop's text had you remember, are
one; and the order can no longer be got wrong. `--no-push` stops after the commit if the
push is not yours to make.

## 6. Report, then next epic


Per epic, report: state on entry, whether architecture was designed (and whether
auto-accepted), tasks created / closed / gated, waves run, **what folded in** — which docs
fold-in ① edited and which ADRs and architecture docs fold-in ② produced, or explicitly that
the epic predated the flow and had nothing staged — and the **four health signals**:

| # | Signal | Bad value |
|---|---|---|
| ① | First-pass PASS rate across every lens that ran | above `signals.baselines.first_pass_ceiling` sustained = soft gate; below `first_pass_floor` = underspecified tasks |
| ①b | **L4 dispatch rate and finding rate** | L4 never firing across a wave that touched a declared `security.path` = a broken trigger, not a clean wave |
| ①c | **Megafiles that grew** (`/swarm` 8b accretion check) | any file past `signals.megafile_lines` growing while its decomposition task sits untouched |
| ①d | **Analyst gate rate** — epics reaching §3b without an `ADEQUACY:` verdict, or §3e without an `AUDIT:` verdict | **any** is a skipped gate, not a clean run. Report the finding rate too: an audit that never finds anything across several epics is a soft lens, and this is the lens whose softness produced the ~36% first-pass rate |
| ② | Escape rate (`fix:`/`revert:` share of last 200) vs `signals.baselines.escape_rate` | rising materially above your baseline, or any `revert:` of a campaign commit |
| ③ | Changed lines per satisfied acceptance criterion | any task over ~400 lines = the slicing rule slipped |
| ④ | Wave yield + merge conflicts | <60% yield, or any conflict (a conflict is a step-3 miss by definition) |
| ⑤ | **Dispatchable-per-epic on entry** — how many tasks were actually ready when you entered each epic | **1 or 2 is the number that explains a bad cost ratio.** Per-epic planning (survey + architect + planner + audit) costs 500–800k tokens and is designed to amortise across a wave of six. Amortised across one task it is 6× the intended cost per landed task, and no pipeline tuning fixes that — the epic is decision-blocked, not the process |

**And what it cost.** `make models-cost` (`${CLAUDE_PLUGIN_ROOT}/harness/checks/models-cost.sh`)
reads the `harness.dispatch` series: cost, turns, cache rate, kills and `results%` per
tier. Every agent in this loop goes through the dispatcher, so that figure IS the
campaign's agent spend — it was 3% of it while the read-only agents went through the
Agent tool. Report the total, the kills, and any tier whose fail% or escalations moved.

**The epic is not a log.** Wave and campaign narrative goes to
the epic's staged `run-log.md`, not to the epic's notes. Only a pointer and current
state belong on the task.

This is not tidiness. The beads backend keeps description and notes in one record and refuses past roughly
64KB, at which point the task rejects **every** update including a 120-character one.
One epic reached 64,244 characters — 99% notes, six wave entries and three campaign
entries among them — and is write-locked permanently. This loop writes to epic notes on every run, so a
write-locked epic means the next run believes it recorded state it did not, and the run after
reads a stale note as current. **Check the exit status of every epic-note write** and report a
failure rather than continuing. `${CLAUDE_PLUGIN_ROOT}/harness/checks/check-record-size.sh` warns before a task gets there.

Owner decisions are the exception and must NOT move: they outlive the epic and
the staging folder does not survive close. They stay on the `decision` task verbatim, and fold
into the corpus if they change a contract.

**The signals are computed and recorded, not narrated.** At epic close (`MODE=interactive`;
`campaign.sh` does it for `auto`, with the outcome the session reported):

```bash
${CLAUDE_PLUGIN_ROOT}/harness/swarm/campaign-signals.sh <epic-id> --outcome closed --mode interactive --record     # or --outcome parked | stopped
```

Every input is on disk — the wave manifests (rounds, L4, merges, conflicts, closes), the
dispatch telemetry (every agent that ran, with its cost and outcome), the epic's `ADEQUACY:`
and `AUDIT:` notes (signal ①d counts them), and git — so the eight numbers are arithmetic,
and the eleven-key payload the loop used to have you type by hand is derived. `--record`
writes an **event** record — invisible to `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh ready` and `list --status=open`, exported to
the tracked export, so the series is versioned with everything else. Run
`${CLAUDE_PLUGIN_ROOT}/harness/campaign/campaign-telemetry.sh` with no arguments to read it back; it flags any value outside the
bands above and, from three epics on, prints the direction each signal is moving.

**A single bad epic is noise. A signal drifting across five is the finding**, and that is exactly
what a narrated report cannot show you — this is the only instrument for noticing the pipeline
degrading, and until now it had no memory. Record even a partial payload: a missing key prints as
`—` and still anchors the trend either side of it.

**An epic that did not close records its outcome.** Parking (§3, §4) and stopping are outcomes
too, and their payloads are the ones where `dispatchable_on_entry` matters most — that is where
planning cost was paid and nothing landed. `--outcome parked` (or `stopped`) files them as
what they are: the reader prints every row with its outcome and draws the trend through
**closed** epics only. Without the flag a parked epic is filed as closed with
`beads_closed: 0` — a catastrophically bad completed epic, bending the trend line through
it. In `MODE=auto` the outcome comes from your return contract's outcome line, so put it
first.

**Being stopped by a human.** `Esc` interrupts you; `/tasks` stops individual in-flight
workers. Neither cleans up — claims stay held, the merge slot may be stuck, and `export.auto`
stays disabled. **`/halt` is the cleanup**, in `pause` mode (keep the claims, gate the epic,
resume later) or `release` mode (hand the tasks back to the queue). Say so in your report if
you are interrupted.

**Report the gating ratio every run, as one line.** It is the number that tells the owner
whether the backlog is buildable at all:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/checks/gating-ratio.sh
```

**A campaign that lands one task per epic is not an inefficient campaign — it is a campaign
running against a gated queue**, and the fix is `/decision`, not process tuning. Say so
explicitly in the report rather than letting the low task count read as a pipeline failure.

**Stop when the epic queue is empty**, or a circuit breaker trips. Final report: epics
completed, epics gated **and the decision owed for each — counting DECISIONS and MISSING
REQUIREMENTS separately**, total tasks closed, health signals
across the run, and a fresh `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh list --type epic --status open`.

## The epic boundary is where the session ends — `MODE=interactive`

**Every request you make re-reads everything before it.** An epic leaves ~300k tokens in
your context; the next epic's ~110 requests carry that for ~33M tokens — about what the
whole orchestrator cost on the measured one-epic campaign — and buy nothing with it, because
the next epic is loaded fresh from the tracker anyway. The CLI compacts only near the
window's end (a measured session reached 920k without it), and nothing you can call clears
or compacts a session. So in `MODE=interactive` an invocation is **one epic**:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/swarm/pinned.sh --always     # must show: no claims, slot free, no worktrees
```

When it does, end with exactly this and stop: *"Epic `<id>` closed and pushed; nothing in
flight. `/clear`, then `/campaign` to continue."* Everything the next epic needs is on disk —
the tracker, the export, the pinned state — which is why it is `/clear` and not `/compact`:
at this boundary a summary is the only thing that can be wrong, and there is nothing worth
summarising that the tracker does not already hold. Mid-epic the opposite holds — a
compaction there loses in-flight ids (measured: 2 of 9 kept), and the pinned-state hook
exists to recover from it, never as the plan. The cost of starting fresh is one ~60k-token
prefix and re-reading this skill: under a dollar against ~$17 an epic of carried context.

`MODE=auto` in a terminal cannot end its own session, so it continues and pays the
carrying cost. **Unattended across epics, run it as `swarm/campaign.sh` instead**: one
fresh `campaign-orchestrator` session per epic through the dispatcher, with a tier, a
ceiling, a sandbox and a cost record, and the tracker as the only state that crosses the
boundary. Measured before it shipped: from inside its sandbox the orchestrator ran this
loop's pre-flight, tracker writes, a nested worker, the merge, the gate and the push with
zero denials.
