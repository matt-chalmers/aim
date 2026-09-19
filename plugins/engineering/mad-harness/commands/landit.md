---
description: Conclude recent work — adversarially test, update docs, sync tasks, commit & push
argument-hint: "[optional scope note, e.g. 'just the auth refactor']"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/harness/*), Bash(git:*), Bash(make:*), Read, Edit, Write, Glob, Grep
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

# LandIt ✈️

Bring the recent work in this repo to a clean, durable landing. Do **not** stop
at "the code seems to run" — the job is done only when the work is adversarially
tested, fully documented, reflected in the tasks backlog, and pushed to the remote.

You are authorized to **fix and finish autonomously**: write the missing tests,
update the docs, create/update tasks, then commit and push. Do the work; don't
just report on it. Ask me only if you hit a genuinely destructive or ambiguous
decision (e.g. a schema migration that could lose data, or a force-push).

Optional focus for this run: **$ARGUMENTS**

---

## Phase 0 — Establish what "recent work" means

Before touching anything, build an accurate picture of the session's work.

- Determine the set of changes to land - all changes since our last landit command.
- Identify the features, actions, endpoints, and data models that were touched.

Summarize this scope back to me in a few lines, then proceed.

## Phase 1 — Adversarial testing (the bar is high)

Refresh our understanding of what tools are chosen for the project, check our specifications for the canonical test / lint / typecheck / build / e2e commands.

Then ensure **strong coverage across every layer that applies to this repo**:

- **Unit** — core logic, edge cases, error paths, boundary values. Think like an
  attacker: null/empty/oversized inputs, unicode, concurrency, off-by-one,
  permission boundaries, malformed payloads.
- **API** — every endpoint touched: happy path, auth/authz failures, validation
  errors, wrong methods, idempotency, pagination, rate limits.
- **UI** — components/flows touched: render, interaction, loading/empty/error
  states, accessibility of new elements.
- **E2E** — the real user journeys through the changed features, start to finish.

Explicitly verify **persistence**: for every create/update/delete action, assert
the change is actually written and survives a re-fetch / reload / new
session — not just held in memory or an optimistic UI cache.

**Migrations & rollback** — if this work added or changed a schema or data
migration, verify it applies cleanly forward *and* has a tested down/rollback
path, and that existing data survives the change. This is a place to **pause and
confirm with me** before running anything destructive against real or shared data.

**Test data seeding** — ensure seeds/fixtures/factories contain *enough* data to
exercise the above meaningfully (multiple records, related entities, edge-case
rows, and at least one "large" case). Extend the seed data where it's thin.

Run the full suite (plus lint, typecheck, and build). Fix what's red — whether
the fault is in the tests or the code — and re-run until green. If a genuine
product-behavior question blocks a fix, pause and ask me.

## Phase 2 — Documentation & decisions

Capture the thinking, not just the diff, so future-us isn't archaeologists.

- Update **specs / design docs** for anything whose behavior, contract, or data
  model changed. Record notable **decisions and their rationale** (an ADR or the
  project's equivalent) — what we chose, what we rejected, and why.
- Update **READMEs**, setup/usage instructions, and any **API docs** or
  schema definitions affected by the work.
- Update inline docs/comments where the code's intent is now non-obvious.
- Refresh changelogs, env-var docs, or config references if touched.

Grep for now-stale references (renamed things, removed flags, old endpoints) and
fix them. Leave the docs describing the code as it is *now*.

## Phase 3 — Tracker backlog hygiene

Use the tracker to bring the tracker in line with reality.

```
!${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh list --status in_progress
!${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh ready
```

- **Close out** completed work from this session: move the relevant tasks to
  done/closed with a short note on what shipped, and link the commits.
- **Update statuses** for anything advanced, blocked, or reprioritized during the
  session.
- **File new tasks** for every follow-up the work surfaced — TODOs, known gaps,
  tech debt, deferred edge cases, flaky tests — with enough context and the right
  dependencies so they're actionable later. Don't let discovered work evaporate.
- Run `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh export` after the closes — the tracked export is stale until you do,
  and `git status` can look clean because the on-disk file matches HEAD — then ensure it is
  part of the commit so the git-synced backlog travels with the code.

## Phase 4 — Cleanup & hygiene

Tidy the working state before it becomes permanent.

- **Dead code & debug cruft** — remove stray `console.log`/`print`/`debugger`
  statements, commented-out blocks, scratch files, and leftover TODOs. Convert any
  TODO that represents real follow-up work into a task rather than deleting it.
- **Dependency & lockfile hygiene** — ensure every lockfile your stacks produce is
  present, committed, and in sync with its manifest. Remove unused dependencies the
  work left behind, confirm no accidental or duplicate additions, and run the
  ecosystem's vulnerability audit if one is available.

## Phase 5 — Commit & push any remaining changes

Only once tests are green, docs + tasks are updated, and the tree is clean:

- Stage any further changes. Split into logically distinct commits if the
  work spans separable concerns.
- Write clear commit messages: what changed and *why*, referencing the relevant
  task IDs.
- Push to the correct remote branch. Never force-push without explicit
  confirmation.
- **Working-tree cleanliness** — after committing, `git status` must be clean: no
  orphaned temp files, build artifacts, or unintended untracked files. Add
  anything that belongs in `.gitignore`; stage anything that should ship.

```
!git status --short
```

## Final report

End with a concise landing report:

- **Scope landed** — features/areas concluded.
- **Tests** — layers covered, counts, coverage delta if available, and the exact
  commands run (all green).
- **Docs** — files updated and key decisions recorded.
- **Tasks** — closed, updated, and newly created (with IDs).
- **Git** — commits made, the branch/remote pushed to, and confirmation the
  working tree is clean.
- **Loose ends** — anything intentionally deferred (and the task tracking it).

The plane is only "landed" when every item above is true. If any of it couldn't
be completed, say so plainly at the top and explain why.
