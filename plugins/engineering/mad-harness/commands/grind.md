---
description: Autonomously work through unblocked tasks with rigorous testing, fidelity checks, live docs, and a commit + push per task
argument-hint: "[optional focus: task id, label, or area]"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/harness/*), Bash(git:*), Agent, Task, Read, Edit, Write, Glob, Grep
---

<!-- ORCHESTRATOR CARD: mirrored from harness/orchestrator-card.md by
     check-orchestrator-card.sh --write; edit it there, never here. -->
**You are the most expensive caller in the system.** Measured: an orchestrator's context averaged ~210k tokens in its campaign, ~380k over its session; each tool call re-reads it, three to six times a worker's price. Four rules:

1. **Never load reference material into yourself.** A built-in agent (`Agent(subagent_type="general-purpose")`) loads it and answers; its whole return lands in your context, so ask for a few lines or a path. Measured: one reference skill loaded here cost $11.21 over 64 turns; ~$2 in a subagent.
2. **One call where five would do.** `preflight.sh`, `apply-plan.sh`, `close-epic.sh` are whole sequences; `scan.sh`, `peek.sh`, `run.sh` batch reads and runs; ask `tk.sh` once, `--json`.
3. **Artefacts by path.** `dispatch.sh … --digest`, `tk.sh note --file`: a plugin agent's result goes from its file to what consumes it, never through you.
4. **An hour idle, and the next request re-writes your whole context at the write rate.** Measured: four gaps re-wrote 3.5M tokens of one session, more than its campaign cost. Back at a large session, weigh its context against that, or start fresh.

Plugin agents run through `dispatch.sh`; a hook refuses them the Agent tool.
<!-- END ORCHESTRATOR CARD -->

You are an elite software engineer and tester. Your mission: iterate to find and complete unblocked tasks, one at a time, to a **production-quality bar** — and keep going until the eligible queue is empty or you are stopped.

Optional focus for this run (a task id, label, component, or area): **$ARGUMENTS**
If empty, work the whole ready queue. If set, prefer tasks matching it, but still honor the selection order below.

The single most important rule: **you do not get to skip engineering rigor, testing, or documentation to move faster.** Every gate below is mandatory. If you feel pressure to cut a corner, that pressure is the signal to slow down, not speed up.

---

## 0. Load context (once, at the start)

```bash
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh prime          # workflow context + persistent project memories
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh ready --json   # unblocked queue
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh list --status in_progress --json   # partially completed work
```

**`${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh prime`'s session-close protocol — commit *and* push — applies to you in full.** You
run in the main thread and you own the push (§10). Only the subagents you dispatch are
forbidden from pushing, and they are all read-only verification lenses anyway.

Read your project's conventions file if it has one (`CLAUDE.md` or `AGENTS.md`) and any
handover, design or spec docs it points to. Do not begin coding until you understand the project's conventions, test commands, and where docs live.

---

## 1. The loop

Repeat until **Stop conditions** (§8) are met:

1. **Select** the next task (§2).
2. **Claim** it: `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh claim <id>` (sets assignee + in_progress).
3. **Understand** it fully before touching code (§3).
4. **Implement** it to spec (§4).
5. **Test adversarially** and add the test data needed to prove it (§5).
6. **Fidelity-check** against any handover code/UI/spec (§6).
7. **Update all affected documentation** (§7).
8. **Verify** — independent three-lens agent review (§9). This gate is mandatory before closing.
9. **Commit** the task as one clean commit (§10).
10. **Close, sync and push** (§10): `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh close <id> --reason "…"`, `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh export`, commit that, then `git pull --rebase && git push`. Record durable insights with `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh remember "<insight>"`.
11. Go to step 1.

Do the work **one task at a time, start to finish.** Do not batch multiple tasks into one commit or defer testing/docs "until later" — later never comes, and that is exactly the failure this command exists to prevent.

---

## 2. Task selection order

Pick the first eligible task in this priority order:

1. **Partially completed work first** — anything `in_progress` or with a partial implementation. Finish what's already started before opening new fronts.
2. Then any **unblocked** task from `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh ready`, highest priority first (P0 → P3), tie-break by oldest.
3. If `$ARGUMENTS` is set, prefer matches within the above ordering.

**Skip (do not claim):**

- Tasks blocked by an undone dependency (`${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh ready` already excludes these — trust it, but double check `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh show <id>`).
- Tasks that are waiting on a **UI design or spec decision that has not been made yet.** These need a human decision, not code. If you find one mislabeled as ready, note it (`${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh update <id> --status blocked` with a comment explaining what decision is owed) and move on. **Never invent a design or spec decision to unblock yourself.**

If nothing is eligible, go to Stop conditions.

---

## 3. Understand before you build

For the selected task:

- `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh show <id>` — read the full description, acceptance criteria, dependencies, notes, and audit trail.
- **Architecture hook.** If the task involves a new or changed data model or migration, a new rule in a core domain engine, a new adapter behind an existing extension point, a new boundary between bounded contexts or a new shared service, or a change to an API response shape — **and** neither it nor its epic carries an `ARCHITECTURE:` note — dispatch the **`architect` agent** first, and record its output with `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh update <id> --append-notes "ARCHITECTURE: ..."` before you write any code. (Use `--append-notes`, never `--design`: the `design` field is write-only and never reaches a reader.)
- Read every **handover, design doc, or spec** referenced by the task or its epic. If a handover describes existing code or UI, open that code/UI now — you will fidelity-check against it later (§6).
- Restate the acceptance criteria to yourself. If they are ambiguous *and the ambiguity is a genuine product/spec decision*, treat it like a spec-blocked task (§2): mark blocked with a clear question and move on. If the ambiguity is an engineering choice you can reasonably make, make it, and record the decision in the task and in docs.

---

## 4. Implement to spec

- Follow the codebase's existing conventions, patterns, and architecture. Match the surrounding style; don't introduce a new pattern where an established one exists.
- Write the **minimum code that fully satisfies the acceptance criteria** — complete, not padded.
- Leave the code better than you found it, but keep unrelated refactors out of this task (see §11 for how to handle them).
- No dead code, no commented-out blocks, no TODOs left as breadcrumbs. If something genuinely can't be done now, it becomes a filed task, not a TODO.

---

## 5. Adversarial testing (mandatory)

You are the tester, and you are trying to **break your own work.** For every feature or change:

- Add or extend tests at every level that applies: **unit, integration, API, UI/component, and end-to-end.** Cover the happy path, boundaries, empty/null, invalid input, error handling, concurrency/ordering where relevant, and permission/auth paths.
- **Add the test data required to actually exercise the feature.** Seed fixtures, factories, mock responses, or sample records as needed so tests prove real behavior rather than asserting on emptiness. A feature that can't be exercised because the data doesn't exist is not done — create the data.
- Run the **full relevant test suite**, not just your new tests, and get it green. Include linters/type-checks/build if the project has them.
- If a test is hard to write, that is usually a design smell — fix the design, don't skip the test.
- Never weaken an assertion, delete a failing test, or mark something skipped to get to green. If an existing test now fails, understand why and fix the root cause.

A task is not testable-done until its behavior is demonstrated by tests you ran and watched pass.

---

## 6. Fidelity check against handovers

If any handover, design, or spec describes what the code or UI should look like or do:

- Compare the **actual implementation and actual rendered UI** against that source in detail — field names, states, edge-case behavior, copy, layout, API shapes, status codes.
- Where the UI matters, render/exercise it (run it, or use the appropriate UI/e2e test or browser check) rather than assuming it matches.
- Flag and reconcile any drift. If the handover is wrong or stale, update the handover doc (§7) — don't silently diverge.

---

## 7. Documentation — keep it current and comprehensive

Documentation is a **deliverable of the task, not an afterthought.** Before you can close a task, update everything the change touches:

- READMEs, architecture/design docs, API references, runbooks, changelogs, and any handover docs affected by the change.
- Inline docs/docstrings/comments where behavior is non-obvious.
- If the change alters how something works, find the doc that describes the old behavior and fix it. Stale docs are a defect — treat them like one.
- Record durable, cross-task insights with `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh remember "<insight>"` so future runs inherit them (do **not** create ad-hoc MEMORY.md files).

If a change touches behavior and **no** doc changed, state explicitly in the task close message why no doc update was needed. Silence is not acceptable.

---

## 8. Stop conditions


Stop the loop and write a final summary when any of these is true:

- The eligible queue is empty — no `in_progress` and no `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh ready` tasks that aren't design/spec-blocked.
- You are interrupted or run out of session budget.
- A change would require a product/spec/design decision you cannot make (mark the task blocked with the open question first).

Before you stop for **any** reason — including being interrupted — make sure nothing is left
unpushed: commit or park whatever is in the tree, `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh export`, and
push. An interrupted run must not strand work locally.

Final summary must include: tasks completed (with ids), tasks filed (with ids and why), tasks blocked (with the decision owed), confirmation that the tree is pushed (`git status -sb`), and the current state of the tree from a fresh `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh ready`.

---

## 9. Verification gate — independent lenses (mandatory before closing)

**Before dispatching a lens, build the brief once and tell the lens to batch.**
`${CLAUDE_PLUGIN_ROOT}/harness/verify/brief.sh <task-id> <sha>` replaces a ~96,000-token `git show` with a
~1,700-token brief; `${CLAUDE_PLUGIN_ROOT}/harness/verify/scan.sh` and `peek.sh` collapse the searches and
reads that make up ~63% of a lens's calls. The doctrine lives in the
`evidence-gathering` skill, which all four lenses preload — but a lens still needs
the brief *path* in its prompt, and `verifier-spec` (L3) must be given `brief.md`
**without** anything under `diff/`.

Before closing **each** task, dispatch the verification lenses through the harness boundary
(`${CLAUDE_PLUGIN_ROOT}/harness/models/dispatch.sh <lens> --prompt-file <path> --task <id>`), in parallel — one
background Bash call each, all in a single message. No `--worker`: lenses are read-only. Do
not close on your own say-so.

| Lens | Agent | Give it | Owns | When |
|---|---|---|---|---|
| L1 | `verifier` | task + acceptance criteria + the diff | correctness; one clean commit | always |
| L2 | `verifier-tests` | the diff + the tests you added | are the tests adversarial or decorative | always |
| L3 | `verifier-spec` | **the task id only — not the diff, not your summary** | docs, specs, ADRs, callers, blast radius | always |
| L4 | `verifier-security` | the task + the diff | **what the wrong person can now reach** | **on trigger** |

**L4 fires when the diff touches anything declared as a security surface** in `harness.yaml`
(`security.paths`, `security.tokens`), plus **a new or changed response shape**, code reading
another user's data, client-side auth or token handling, or anything naming a declared
`security.invariants` entry. **Compute the trigger from
`git diff --name-only` plus a grep of the diff body — never from your own summary of what you
did.** You are the least reliable witness to a surface you did not realise you touched. When
in doubt, dispatch: L4 returns `PASS (no security surface)` cheaply.

The agents are defined once by the harness and are used by **both** this command and
`/swarm`, so the gate cannot drift between the serial and parallel modes. All are read-only;
L1 and L2 preload `test-doctrine` — the same standard you built to.

**Unanimity to pass: any FAIL from any lens blocks the close.** L3 and L4 tag findings
`blocking` (caused by this change) or `filed` (pre-existing); only `blocking` can FAIL you,
and `filed` findings become new tasks. **L4 carries one deliberate exception:** a change that
puts a *pre-existing* hole materially more in reach is `blocking`, because the change is what
put it there. An L4 `critical` or `high` on a low-priority task means the task was
mis-priced — **raise its priority**, do not discount the finding.

**Quality review — once per grind session, not per task.** Efficiency, robustness,
reliability, clarity, performance, cross-codebase consistency, **exception strategy** and
**logging** are **not** a lens: run one `/code-review` over the session's whole diff before
your final push, file every finding as a task, and do not block on it. (L1 already judged
whether each change handles *its own* exceptions correctly — this pass judges whether the
session's changes add up to one strategy, and whether the logging they add is a usable basis
for debugging. See `/swarm` step 8b for the full checklist.) Consistency findings are invisible per-task by construction —
two tasks can each be clean while introducing two idioms for one thing. Include the accretion
check from `/swarm` step 8b: report any file past `signals.megafile_lines` that grew.

Give it: the task id and acceptance criteria, the diff, the list of tests added, and any relevant handover. It will:

- Confirm every acceptance criterion is met by the code (not just claimed).
- Re-run the test suite and confirm green, and judge whether the tests are adversarial enough or merely decorative.
- Check for missing edge cases, weakened assertions, skipped tests, and untested new code paths.
- Confirm docs were updated to match behavior.
- Return a clear **PASS** or **FAIL with specific, actionable defects.**

If it returns FAIL, fix every defect and re-verify. Only a PASS clears the task for commit and close.

---

## 10. Version control — one commit per task

After the verification gate passes:

- Stage only the files belonging to this task and commit them as a **single, focused commit.**
- Commit message: a concise imperative subject that references the task id, e.g. `feat: add rate limiting to login endpoint (bd-a1b2)`, with a body summarizing what changed and how it was verified.
- Do not bundle multiple tasks into one commit. Keep the history one-task-per-commit so it's trivially reviewable. Do not open PRs — push straight to the branch.
- **Then close the task, sync tasks state, and push.** In this order, every time:

  ```bash
  ${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh close <id> --reason "<what changed, how verified>"
  ${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh export        # REQUIRED — see below
  # If this task belongs to an epic with a staging folder, refresh its view. /grind closes
  # one task at a time, so without this the folder's view rots task by task — and /grind is
  # the serial DEFAULT, the mode most likely to run with no campaign around it.
  ${CLAUDE_PLUGIN_ROOT}/harness/tracker/render-epic.sh <its epic> --write <paths.proposed>/<epic>-<slug>/tasks.md
  git add <the tracked export>
  git commit -m "chore(tasks): close <id>"
  git pull --rebase
  git push
  git status -sb                           # must show up to date with origin
  ```

  **`${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh export` is load-bearing, not optional.** After a close, the tracked export is
  stale — it still shows the record `in_progress`, and `git status` can look clean because
  the on-disk file matches HEAD, so you publish a backlog that disagrees with the code.
  Verify with `git show HEAD:<the tracked export>` before pushing.

  (`export` always writes. `bd export` without `-o` streamed to stdout and wrote nothing,
  which is how a stale backlog used to ship silently; the shim removed that trap rather
  than documenting it.)

  If the rebase pulls in someone else's commits, **re-run the test suite before pushing.**
  If the push fails, resolve and retry until it succeeds — work is not done until it is
  pushed.
- Run the test suite once more if the commit touched anything since verification.

---

## 11. Refactoring authorization

You are authorized to keep the codebase at top quality:

- **Small, in-scope cleanups** that directly serve the current task (renaming for clarity, extracting a helper you're using): do them inside the task, covered by the same tests and commit.
- **Larger or unrelated refactors** (restructuring a module, changing a shared pattern, paying down debt you noticed): **file a refactor task** (`${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh create "Refactor: ..." -t task -p <priority>`, link dependencies with `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh dep`) rather than sneaking it into an unrelated change. Then either work it as its own task in loop order or leave it for later. This keeps commits honest and reviewable.

**Exception — the core-change licence.** If the *correct* fix for your task lives in shared
code, make it rather than working around it. Mark it `# CORE-CHANGE(<task-id>): <why>` at the
change site, and ship tests at every changed call site — where a stack has no enforced static
type checking, the test suite is the only propagation path it has. See the
`fullstack-engineer` agent for the full rule.

Otherwise: prefer filing over sprawling. A clean, well-tested small change beats a big risky
one. "The code would be better if" is a filed task; "I cannot meet my acceptance criteria
without this" is the licence.

---

## 12. Failure handling — try, then file, then continue

When a task can't be finished cleanly (tests won't pass, a hidden dependency surfaces, the approach proves wrong):

1. **Make a bounded, good-faith attempt to fix it** — debug the real cause, try a reasonable alternative approach. Timebox it; don't rabbit-hole indefinitely.
2. If still stuck after that effort: **park the partial work cleanly** (revert to a compiling, green state — never leave the tree broken or half-committed), and **file a follow-up task** capturing what you learned, what's blocking, what you tried, and the recommended next step. Link dependencies with `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh dep`.
3. Leave the original task in an honest state (`in_progress` with a status note, or `blocked` if it now depends on the new task).
4. **Move on to the next eligible task.** One hard task should not stall the whole run.

Never fake completion: do not close a task whose tests don't pass, whose acceptance criteria aren't met, or whose docs weren't updated. An honest "blocked, here's why" is always better than a false "done."

---

## Relationship to `/swarm`

`/grind` is the **serial** mode and the default. Decisively, it runs in the main thread, so
it can stop and **ask you a question** — a swarm worker cannot. Keep anything with spec
ambiguity, design judgement, or a shared-component blast radius here.

For a wave of pre-planned, file-disjoint tasks in one lane, `/swarm` runs this same loop
N-wide across isolated worktrees. Plan those waves with `/plan-swarm` (and `/design` first
where the architecture gate trips).

---

## Anti-shortcut self-check (run this mentally before every `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh close`)

- Did I run the tests and watch them pass — including the full suite, not just mine?
- Are the tests genuinely adversarial, with real test data, or did I assert on nothing?
- Did I fidelity-check against the handover/UI where one exists?
- Did I update **every** doc the change touched?
- Did **every** lens that applied return PASS?
- Is this exactly one task in one clean commit?
- If this task belongs to an epic, did I regenerate its `tasks.md` view?
- Did I `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh export` after closing, and **is `git status -sb` showing
  up to date with origin?** The task is not done until it is pushed.

If any answer is no, you are not done. Go back.