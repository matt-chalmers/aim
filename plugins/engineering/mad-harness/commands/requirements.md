---
description: Take an outstanding REQUIREMENT task and specify it with the owner, interactively, until a planner could cut tasks from it
argument-hint: "[task-id, or empty to pick the highest-value one]"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/harness/*), Task, Read, Write, Edit, Glob, Grep, Bash(git:*), Bash(ls:*), Bash(wc:*), AskUserQuestion
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

Specify one outstanding requirement with the owner: **$ARGUMENTS** (empty = pick the
highest-value open `REQUIREMENT:` task).

`/decision` resolves a **fork** — options exist, the owner picks one, it takes one question.
**This command fills an ABSENCE.** Nobody has said what the feature does, so there are no
options to offer and no single question to ask. It takes a conversation, and the output is a
specification a planner can cut tasks from.

**Resolve exactly one per invocation.** Two half-specified features are worth less than one
finished spec.

---

## The line you must not cross

**You are not writing the spec. The owner is.** Your job is to make it cheap for them.

The failure mode this command exists to prevent is an agent inventing scope and asking the
owner to ratify the invention — which is precisely what the `REQUIREMENT` task was filed to
stop. Ratifying a plausible-looking spec you wrote feels like deciding, and is not.

So, throughout:

- **Where the answer is already in the repo, find it — do not ask.** An adjacent feature doc,
  an ADR, a settled owner decision, the entity index. Bring it as *"this already says X, does
  that hold here?"* — a confirmation, not a question.
- **Where genuine options exist, offer them** with the concrete cost of each. That half is
  `/decision`-shaped and `AskUserQuestion` is right for it.
- **Where there is no basis to propose, ask openly.** Do not manufacture three plausible
  options to make the question look answerable. *"What should happen when a notification fails
  to deliver?"* with no options is a better question than three you invented.
- **Label every reading as a reading.** *"My reading is X — correct me"* is honest.
  *"I recommend X"* is not, when X is scope rather than approach.

---

## 1. Pick the task, and verify the gap is still real

```bash
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh list --type decision --status open --limit 50 --json    # REQUIREMENT: tasks are type decision
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh gate list                                               # what each blocks
```

If `$ARGUMENTS` names a task, go to it. Otherwise rank by **how much work the answer releases**
— an epic parked behind it beats a leaf — and report the shortlist in one short table before
investing.

**Then re-verify the premise, exactly as `/decision` does.** Requirement tasks are written at a
moment in time and the ground moves:

- **Has a later owner decision already specified part of it?** Search `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh list --type decision`
  including **closed** ones, the epic notes, and the decision-record directory.
- **Does a feature doc already exist** that the task's author missed? List `paths.features`.
- **Is the epic still open**, and does anything still depend on it?

Say plainly which you found: *premise holds* · *partly specified already* (narrow the task
first, and say what you narrowed) · *already specified* (close it, cite where) · *dissolved*.

---

## 2. Read everything that constrains the answer — before the first question

This is the step that earns the command. Every minute here removes a question the owner would
otherwise have to answer from nothing.

```bash
cat <paths.index>                      # entity index, endpoint index, decision log, feature index
ls <paths.features>/                   # is there an adjacent feature that answers half of this?
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh memories                            # recorded traps in this area
```

**If the parent epic already carries a `SPEC INDEX`, hand it to the analyst.** A campaign run
may have built one at §3a before parking the epic; it is a regenerated cache, so it is cheap to
confirm and cheaper than rebuilding. The analyst still surveys — it is verifying and extending,
not trusting.

**Dispatch `analyst-survey`. This is required, not an optimisation** — skipping it
means asking the owner questions this repo already answers, which is the one thing this command
exists to prevent. It is fan-out research across the
whole corpus, it is read-only, and you are about to be in a live conversation where you will do
it shallowly under context pressure. It returns what is already specified, any two accepted
documents that conflict on this ground, the settled decisions that bind, what the IA and NFRs
fix, and — most valuable — an ordered list of what is **genuinely open**, structural questions
first. That list is your agenda.

It also returns a **DO NOT ASK** list: questions this repo already answers. Honour it. Asking
the owner something `information-architecture.md` already fixes spends the one resource this
command exists to conserve.

Gather from its return, and bring to the conversation:

- **Adjacent feature docs** — the closest existing feature is usually 60% of the answer, and
  its shape is the house style the owner will expect.
- **Decision records that already bind this area**, quoted. If two conflict, that *is* the first question.
- **Settled owner decisions** — including closed ones. A closed decision is *settled*, not
  irrelevant.
- **What already exists in the code**, if anything. A half-built feature constrains the spec.
- **The product-level docs** — overview, information architecture, NFRs, glossary, build
  sequence. The IA in particular constrains where a feature can even live, often to a fixed set
  of destinations. Do not ask a question whose answer the IA already fixes.

**Report what you found before asking anything**, so the owner can see the starting point and
correct it. Frequently they will say *"most of that is right, and here's the bit you're
missing"* — which is far cheaper than answering twelve questions.

---

## 3. Open the working file

Create the draft at:

```
<paths.proposed>/<epic-id>-<slug>/proposal.md
```

**The proposed directory, not the features directory** (`harness.yaml` → `paths.proposed`,
`paths.features`). A half-written feature doc in the features directory is dangerous — feature
docs are the source of truth and someone will build from one. The proposed directory is the
staging area for exactly this: durable, indexed, and explicitly **not** a claim about the
running system. See the `spec-lifecycle` skill.

It does not become part of the corpus here. It is applied at **fold-in ①** —
`campaign-loop` §3a, once `analyst-survey` returns `ADEQUATE`, before the architect runs — and
the file is deleted then. If the task has no epic yet, keep the notes on the task and create
the file when the work is scoped as one; that is when it gets its stable name.

**Record the path on the task immediately**, so a session death does not lose it:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh note <task-id> "REQUIREMENTS DRAFT in progress at <path> (started <date>). Sections filled: none yet."
```

**A draft may already exist.** When `campaign-loop` §3a returned `ABSENT`, `spec-editor` has
already assembled that proposal from the corpus and `analyst`
judged it still inadequate. **Start there, not from the template.** Every criterion in it carries
the doc and quoted opening words it derives from — your job is to confirm those with the owner
and fill `## Open questions`, which is where everything the corpus could not answer was put.
Opening at *"here are the four we derived, confirm them; these two we cannot answer"* costs the
owner a fraction of starting at zero.

**Read the citations before confirming.** A criterion whose cited clause no longer says what the
draft claims is worse than a blank, and it is the one thing the owner cannot check for you.

Otherwise seed it from that directory's proposal template. It opens with YAML frontmatter carrying
`epic`, `slug`, `status` and `lands_in` — **only what a gate reads**: `analyst-survey` branches
on `status`, and `lands_in` drives the cross-proposal contention check. Never restate body
content up there; a second home for the same fact drifts. The body is
`## What must become true`, `## Base assumed`, `## Acceptance criteria`, `## Open questions`.

Keep `lands_in` accurate as the conversation widens the scope — an unlisted doc is a doc
fold-in will not edit and the contention check cannot see.

**Shape it as a change, not as a feature.** Most epics are not new feature areas — a typical
one touches two or three existing features plus an architecture doc and often a decision
record. Say what changes **in each doc it lands in**. A proposal shaped as a mirror of a single
feature README cannot express a change that spans three, and a brand-new feature area is just
the degenerate case where the change is "create this file".

Write the acceptance criteria to the standard the feature doc will carry, because they are
folded in **verbatim as unchecked criteria**. Record the **base you assumed** — what the docs
you cite said when you wrote this, by quoted opening words, never line numbers —
so a proposal that sits for months can be re-derived rather than misapplied.

**Mark every section with its state** as you go — `TBD`, `DRAFT (my reading, unconfirmed)`, or
`CONFIRMED <date>`. The owner must be able to see at a glance what is theirs and what is yours.

---

## 4. Ask in passes — never one long interrogation

**Order the passes so that each one's answers narrow the next.** Ask the structural questions
first: the ones whose answer changes the *option set* for everything downstream. Getting this
order wrong wastes the owner's answers.

A workable default order — adapt it to the feature:

1. **Purpose and boundary** — what is this for, and what is explicitly *not* in it? The
   out-of-scope half is worth as much as the in-scope half and is usually never written down.
2. **Surfaces and IA** — where does it live? Usually constrained hard by the project's
   information-architecture doc; bring that constraint rather than asking an open question.
3. **The core behavioural rule** — the one sentence the whole feature turns on.
4. **Data** — only after behaviour. Asking for a schema before the rule is how you get a schema
   that cannot express the rule.
5. **Edges and failure** — what happens when it fails, when it is empty, when the actor lacks
   permission, when it races. **This is where specs are thin and where the lens rounds land.**
6. **Interactions** — anything touching a frozen or already-published record, a recompute, or
   another user's data.
7. **Acceptance criteria** — draft them back and have the owner correct, rather than asking
   them to write them.

Rules for each pass:

- **At most 3–4 questions per `AskUserQuestion` call.** More than that and the later answers
  get careless — the same reason `/decision` resolves only one.
- **Use `preview` where the shape is easier seen than described** — a lifecycle sketch, two
  states side by side, the resulting row.
- **Write the answers into the file immediately**, verbatim, before the next pass. The owner
  should be able to read the file mid-conversation and see their own words.
- **After each pass, show what changed** — a two-line summary, not the whole file.
- **If an answer contradicts something already in the file, stop and surface it.** Two
  conflicting requirements caught in conversation cost a sentence; caught in a lens round they
  cost three remediation rounds.
- **If an answer reveals a question you did not ask, ask it immediately.** Decisions
  frequently have a hidden second half; asking again straight away has repeatedly proved far
  cheaper than shipping the wrong one.

**Between passes, offer the exit.** *"That's enough to plan the first slice — carry on, or
stop here and leave the rest as open questions?"* A spec good enough to cut three tasks from
today beats a perfect one next week.

---

## 5. Know when it is finished

**The bar is not "complete". It is: could a planner cut tasks from this, and could a verifier
check them?**

**Dispatch `analyst` in AUDIT mode on the draft before you mark it `ready to fold in`.
Required — a proposal with no audit verdict may not be folded in.** At fold-in ① it becomes
the source of truth every future agent builds from; auditing it afterwards is auditing production.

**You are the worst party to judge this spec**, because you just co-wrote it — the same reason
a swarm worker never verifies its own task. The analyst is independent, and it judges against a
checkable standard: every criterion locatable in a diff, behavioural rather than
implementational, failure and empty and permission states present, every actor named, no
ambiguous quantifier, terms defined against the glossary, no contradiction with an
accepted decision record, the invariant surface stated, and the scope boundary written down.

Route the verdict:

- **`blocking` findings** — a planner could not cut a correct task, or a verifier could not
  check one. **Take them back to the owner in one more pass.** They are still here; that is the
  cheapest moment this will ever cost.
- **`filed` findings** — write each into `## Open questions` with a named owner. Do **not**
  silently absorb them; a blank section reads as "nothing to decide".

**Note what a PASS means here.** A spec that is 80% complete with its gaps *written down* is a
PASS — specs are expected to lead the code, and an honest gap is a visible one. A spec 80% complete and **silent** about the rest is a FAIL, because the silence is what
gets built over.

**The analyst never proposes the missing requirement**, only names its class. If it comes back
with drafted criteria, that is a bug in the dispatch — the owner fills gaps, not an agent.

Then test it yourself:

- Every acceptance criterion is **something `verifier` can locate in a diff.** *"Works
  correctly"* fails; *"Given X, when Y, then Z"* passes. This is the same bar the planner and
  the verifier already hold.
- The **data shape** is defined well enough to write a migration, or explicitly deferred.
- The **failure and empty states** are specified, not just the happy path.
- Anything still unanswered is written into **`## Open questions`** with a named owner —
  **not** silently left blank. A blank section reads as "nothing to decide"; an open question
  reads as "decide this before building it".

**If the owner stops early, that is a valid outcome** — write what you have, mark the rest
`TBD` in `## Open questions`, and say in the report which slices are now plannable and which
are not.

---

## 6. Land it

**The draft becomes a proposal that a planner can cut tasks from, or it was wasted.**

1. **Mark the proposal `ready to fold in`** in its status header. **Do not write the feature
   doc here.** The corpus is edited at **fold-in ①** — `campaign-loop` §3a, once
   `analyst-survey` returns `ADEQUATE`, by `spec-editor` — and the proposal file is deleted
   then. Writing the feature doc now puts an unbuilt spec in the source-of-truth folder, which
   is the failure this whole flow exists to stop: a stale status header in a feature doc has
   led an agent to conclude a substantially built feature was unbuilt.
2. **Name every doc it lands in** in the frontmatter's `lands_in` list — feature docs,
   architecture docs, decision records, and the corpus-index rows fold-in will need. Fold-in
   should be mechanical; a doc nobody named is a doc nobody edits, and a feature absent from the
   index is invisible to every future agent.
3. **A decision the owner settled here is a decision record now** — in `paths.adrs`, at
   `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh adr-next` (the next free number, computed once). Never let a
   worker pick the number; two streams picking independently have collided before. A decision left **open** is a `decision` task plus a **draft record** under
   `paths.proposed`; nothing may cite that as settled, and the proposal is not `ready to fold
   in` while any remain.
4. **Record the answers verbatim on the task**, in the owner's exact words:

   ```bash
   ${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh note <task-id> "OWNER REQUIREMENTS <date>, VERBATIM: '<their words>' ... "
   ```

   Paraphrase loses the constraint that makes it useful. **Watch the ~64KB record ceiling** —
   a note past it fails hard. If the task is already large, the proposal *is* the durable
   record and the note should just point at it. Note that the proposal is
   deleted at fold-in, so anything that must outlive the epic belongs in the feature doc's
   criteria or in an ADR, not only here.
5. **Record the audit verdict** in the feature doc's status header and in the close reason, so
   its absence is visible later:

   ```
   > **Status**: planned · **Requirements audit**: PASS <date> (analyst), N open questions
   ```

6. **Close the task**, citing the doc: `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh close <id> --reason "Specified in <feature doc>;
   N acceptance criteria."`
7. **Un-park the epic:**

   ```bash
   ${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh unpark <epic-id>        # the gate AND the status; a gate resolved by hand does not reopen the epic
   ${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh export                  # the tracked export is stale after the close above; a commit without it publishes a backlog that disagrees with the doc
   ```

8. **Commit** the doc, the index entry, the tracked export and any decision record together, and **push**.

---

## 7. Report, and hand back

- **What is now specified**, and what deliberately is not.
- **The verbatim constraints** the owner gave that a planner must honour.
- **Which tasks could now be cut** — a rough count and shape, not the DAG.
- **Whether the epic came back into the queue.**
- **What remains in `## Open questions`**, and who owns each.
- **The analyst's audit verdict**, and any `blocking` finding you took back to the owner.

**Do not plan it, and do not build it.** This command produces a specification and hands the
queue back. Cutting the tasks is `/plan-swarm`; building them is `/campaign` or `/swarm`.
Planning inside this command would mean the same agent that wrote the spec also decides what
it means — which is the independence this whole pipeline is built on.
