---
name: test-doctrine
description: 'The single testing standard for this repository — what "tested" means, what a decorative test looks like, the mutation-testing method, and the recorded traps that have burned us. Portable: the layout and command sections point at your repository''s harness.yaml and stack modules. Declared by fullstack-engineer (builds to it), quality-engineer (hardens to it), and verifier + verifier-tests (judge against it).'
---

# Test doctrine

One standard, three consumers: the agent that **writes** a change, the agent that
**hardens** someone else's, and the agent that **judges** both. A judge marking against a
weaker bar than the builder built to is how a verification gate silently rots — so this
file is the bar, for all three.

## 1. The stance

**You are trying to break your own work.** Not to demonstrate that it works — anyone can
write a test that passes. Assume there is a defect and go looking for it.

For every change, cover every level that applies — **unit, integration, API,
UI/component, end-to-end** — across:

- the happy path
- boundaries (first, last, zero, one, max, off-by-one)
- empty / null / missing
- invalid input and malformed payloads
- error handling and failure paths
- concurrency and ordering, where the code has any
- permission and auth paths, including the denied case

## 2. Create the test data

**A feature that can't be exercised because the data doesn't exist is not done — create
the data.** Seed fixtures, factory functions, mock responses, sample records, so
tests prove real behaviour instead of asserting on emptiness.

Factories live beside the code they build data for (see §7). **Never commit raw fixture
files** — factory functions only. If several future tasks will need the same data, that
factory work is its own `test`-lane task.

## 3. Decorative tests are a failure, not a pass

A test is decorative if it would still pass with the feature deleted. Specifically:

- asserting on emptiness when the fixture was never populated
- asserting a mock returns what you told the mock to return
- asserting a page rendered, without asserting *what* it rendered
- snapshot tests standing in for behavioural assertions
- a test whose only assertion is `not None`

**Ask of every test: if I deleted the implementation, would this go red?** If not, it is
not a test. This is the primary thing `verifier` hunts for.

**When you actually try it, do it as separate tool calls — never as one shell command.**
Measured in the lab: 22 of 40 worker dispatches were denied for exactly this, every one the
same shape — `cp impl backup && cat > impl <<'EOF' … EOF; <run the suite>; cp backup impl`
in a single Bash call. A compound command matches no permission rule even when every part of it
is granted, so it is denied, the turn is wasted, and you try again. The granted way:

1. **Edit** the implementation to a stub (`raise NotImplementedError`) — the Edit tool, not a
   heredoc.
2. Run the suite through `$HARNESS_ROOT/verify/run.sh` — one Bash call.
3. **Edit** it back, or `git checkout -- <that one path>` — one call, that path only.

Three calls, all granted, and the answer is the same. Once the work is committed, prefer
`mutate.sh` (§5), which does this in a throwaway tree and records the result as evidence.

## 4. Test-first by default — and the red step must leave evidence

**Where the behaviour is knowable before the code, write the test first and watch it fail.**
This is not a new rule so much as the general case of the one most projects already mandate for bug
fixes — *"reproduce locally with a failing test"* — and of §8's *"point a negative test at a
fixture that SHOULD trip it and watch it fail."*

**Default to test-first for:** service and business logic with a knowable input → output;
predicates, guards and boundaries; the rules of any core domain engine; every acceptance criterion phrased as a
behaviour; API contract changes; and every bug fix, without exception.

**Do NOT force it for:** UI fidelity work against a design handover (the verification is a
side-by-side composite, not an assertion); migrations (the round-trip is the test); doc-only
tasks; and adapter work where the provider's payload shape is genuinely unknown until you see
it — there, spike first, then write the tests against the shape you found, then harden.

**THE RED STEP MUST LEAVE AN ARTEFACT, or it did not happen as far as anyone else is
concerned.** This is the part that matters here, and it is why "we do TDD" is not on its own a
sufficient answer:

- The red step happens on your machine and vanishes. `verifier-tests` reviews tests it did not
  write, months of context later. **A claim of discipline is worth nothing to it; a recorded
  red → green transition is evidence.**
- So **paste the failing output into your return** — the test name and the actual assertion
  error, not "it failed as expected". `assert 2 == 1` with the values shown is evidence.
  "I wrote the test first" is not.
- **It pays for itself immediately.** A recorded red step retires the entire *"does any test
  cover this at all"* mutation class for that behaviour, so §5's budget drops rather than rises.

**What test-first does NOT protect you from, so do not treat it as a substitute for §5.** Every
defect the lenses found on a duplicate-record detector passed red → green at every step:
a fixture helper that decorated *both* sides of a two-sided rule with one keyword, so a per-side
column was unpinnable; a loop only ever run with one item; a `!=` rule fixtured in one
direction only; a status that appeared in no fixture. **Test-first disciplines whether you
wrote a test. It says nothing about whether your fixture exercises both sides of the rule** —
and that is this repo's actual failure mode.

## 5. Mutation testing — reconstructing the red step you did not take

**Break the code deliberately and check the tests notice.** §3 asks the crudest version — *if I
deleted the implementation, would this go red?* Mutation testing is that question asked
precisely, one behaviour at a time. Where §4 was not possible or was not done, this is the only
thing that tells you a test discriminates rather than merely passes.

**Use `${CLAUDE_PLUGIN_ROOT}/harness/verify/mutate.sh <task-id> <commit-ish> <mutations-file> [test-args]`** — a plain
call, no environment prefix; put the mutations file under `.harness/run/` in your checkout,
where the script keeps its own trees. Read its header. It builds each tree with `git archive` so a
a stale compiled artefact is impossible, never writes to your worktree, **aborts if a mutation does not match
exactly once**, restores by re-extracting rather than undoing, records failing test *names*, and
re-runs mutant 1 last to prove the batch is reproducible. If it prints `FATAL`, it is telling
you a result **is not evidence** — do not work around it, and never report a run that did not
complete as a survival.

### The taxonomy — pick from these, do not free-associate

| # | Class | Example |
|---|---|---|
| 1 | **Deletion** — remove the feature entirely | return `()` from the entry point |
| 2 | **Per criterion** — one aimed at each AC's specific behaviour | drop one filter the AC names |
| 3 | **Boundary flip** | `>` → `>=`, `!=` → `<`, `and` → `or` |
| 4 | **Predicate narrowing** | drop one clause from a filter |
| 5 | **Per-side argument swap** ⚠️ | `f(left, right)` → `f(right, right)` |
| 6 | **Loop / multiplicity truncation** ⚠️ | `for x in xs` → `xs[:1]` |
| 7 | **Schema-level** | drop a constraint, narrow a column, flip a cascade rule |

**Classes 5 and 6 are marked because they keep finding real defects and are the two most often
skipped.** Both are invisible to test-first: the feature is present and working, and the
*fixture* is what is one-sided. Their tell is a fixture helper that sets a property on both
halves of a two-sided record with one keyword, or a collection built with exactly one member.

### Scope each mutant to the tests that should catch it

**A targeted mutant asks one question: does THIS test catch THIS regression?** Run that test,
not the app. Name the individual tests — the harness forwards runner arguments, so pass them.

**Breadth does not answer the question, and it is most of the cost.** One boundary mutant on a
recent task reddened 35 tests across five files; the useful content of that run was two names.
The other boundary mutant on the same guard reddened 8. Neither number changed a verdict.

**The specificity worry is real but breadth is the wrong fix for it.** A mutant can redden the
expected test for an unrelated reason — it broke setup, not behaviour — and then a kill count
means nothing. **Read the actual `E` line and confirm the assertion that failed is the one the
test exists for.** That is cheaper than a wide run and strictly more conclusive: 35 collateral
reds still would not tell you *why* the expected one failed.

**Two exceptions, and only two:**

| case | why it needs breadth |
|---|---|
| **Class 1, the deletion test** | The question *is* "does **anything** notice this feature vanishing?" Scoping it to the tests you expect would be circular. Run the module or the app. |
| **§4a re-measurement** after touching shared code | The question is "did I disarm a test I did not write?" — inherently about other people's tests. Re-run the mutants **those** tests were built to kill, named from the earlier record. |

**Everything else runs narrow.** If a mutant breaks a test in another app, the full suite and
the wave gate will say so — that is their job, and they already do it. Mutation testing is not
a second regression suite, and using it as one buys nothing the gate does not already give you.

**State the scope beside the count.** `8 red` is not interpretable; `8 red (scoped to
test_dedupe_sanity_floor.py)` is. A count without its scope reads as a measurement of
the suite when it is a measurement of whatever the caller happened to pass.

### Declare your coverage — this is what makes the set reviewable

**A mutant list on its own cannot be judged.** Sixteen mutants that all hit class 3 look
identical, in a report, to sixteen that span the taxonomy. So state the mapping:

```
COVERAGE (against test-doctrine §5)
  1 deletion        ✓ return () from <the entry point>                   18 red
  2 per criterion   ✓ AC2 <filter>, AC4 <predicate>, AC8 <output shape>   1-3 red each
  3 boundary        ✓ != -> <  ·  > -> >=                                 1, 7 red
  4 narrowing       ✓ dropped one clause of <the filter>                  1 red
  5 per-side        ✓ <pred>(left, right) -> (right, right)               2 red
                    ✓ ...and -> (left, left)                              2 red
  6 loop truncate   ✓ <the collection> loop -> [:1]                       1 red
  7 schema          n/a — no model or migration change in this task
  ENUM COVERAGE     <Enum> has 5 members; fixtured: all 5
```

**A class you skipped is fine; a class you skipped silently is not.** Write `n/a` and the
reason. "No collection is iterated here" is a complete answer for class 6. Saying nothing is
not, because the reader cannot tell whether you considered it.

**Two coverage questions are worth answering explicitly every time**, because they are where
this repo's real defects have lived:

- **Every argument of a multi-argument predicate**, when two arguments share a type. `f(a, b)`
  where both are `Account` can be silently reduced to `f(a)` — and the fixture that would catch
  it is one that differs *per side*. A fixture helper that sets a property on both sides with
  one keyword makes this class unpinnable by construction.

  **The REDUCTION is the mutant, and a swap does not substitute for it.** `(a, b) -> (b, a)`
  and `(a, b) -> (a,)` are different mutants that different fixtures kill, and only the second
  one asks whether each side is independently pinned. A swap dies broadly on ordinary traffic
  whenever `a != b`, so it reddens loudly while telling you nothing about one-sidedness.
  **Class 5 is not satisfied until each side has been dropped separately.**

  This is not hypothetical and it is not cheap to miss. One shipped task carried a
  two-sided guard with a class-5 swap recorded as ✓ and **zero survivors** across 21
  mutants. A later lens dropped each side on its own: **both reductions survived the whole
  suite** — over a thousand tests — because the only fixture whose two sides disagreed
  changed *both* of them. The guard, on the operation the task itself called the worst
  blast radius in its epic, could have been cut in half silently. Recovering it cost a remediation dispatch, a second lens round
  and a re-check. Dropping the two extra mutants at authoring time would have cost minutes.
- **Every member of an enum the code branches on.** Name the members and say which are
  fixtured. One recorded enum had **two** unfixtured members while a dedicated test existed
  for a third — nobody noticed until the list was written down.

### Budget, and how not to burn tokens

- **12–16 targeted mutants is the working range**, scaling with AC count — roughly one per
  acceptance criterion plus the deletion test plus the boundaries. Tasks have been well served
  at 16. Going to 40 has found things, but with sharply diminishing returns per token.
- **Aim class 1 once, not once per test.** The deletion test is a single mutant.
- **Never mutate the migration to test an application-level behaviour.** A cascade rule may be enforced by
  the framework's own collector rather than by the database, so a cascade-versus-restrict mutant aimed at a
  migration produces a **false survivor**. Aim it at the model.
- **Watch for the indentation-substring trap.** `    x += f(a)` at 12 spaces is a substring of
  the same line at 16. The harness's exactly-once guard catches it — it has, in production —
  but anchor with enough surrounding text to be unambiguous.
- **`verifier-tests`: verify a worker's mutation log, do not redo it.** The lens gate hands
  the log's path over in the prompt (or says none was found). The harness self-attests
  (exactly-once, name-recording, mutant-1 recheck), so spot-re-run **three** of sixteen and
  confirm the killing tests are the named ones. Re-run in full only when the log is
  self-inconsistent, its recheck line is missing, or the task is high-consequence.
- **Reproduce every claimed "equivalent mutant" rather than accepting it.** That label is the
  easiest place for a real hole to hide. An equivalent mutant is fine — but prove the boundary
  is armed some other way before believing it.

## 6. Never take the cheap way to green

- **Never** weaken an assertion to make it pass.
- **Never** delete a failing test.
- **Never** mark a test skipped or xfail to get to green.
- An existing test that now fails means **find the root cause** — the test is usually
  right and your change is usually wrong.
- A test that is hard to write is a **design smell**. Fix the design, don't skip the test.

## 7. Where tests live, and what this project holds itself to

The doctrine above is portable. This is not — it comes from your repository's config.

Read your repository's `harness.yaml` → `testing:` for the layout, the CI gates and the
coverage targets it holds itself to. `${CLAUDE_PLUGIN_ROOT}/harness/verify/peek.sh harness.yaml` reads it in one
call.

**Every bug fix gets a regression test.** It is the acceptance criterion of the fix, so it
lives in the same task and the same commit — never deferred to a follow-up.

## 8. Recorded traps — these have each cost us a debugging session

**Negative assertions are a race.** `expect(locator).toHaveCount(0)` passes on its *first*
check and never waits to confirm absence. Where a screen's empty state is byte-identical
to its loading state, it is satisfiable before the row renders — so it passes for a user
who *does* hold the thing. Adding a sync point usually *wins* the race, so it looks fine
locally and rots on a slower machine. **Assert the API payload as the control, then assert
the UI agrees**, and arm the response wait *before* whatever navigates.

**A route-matching glob may not match a query string.** A glob typically compiles to an
`^…$`-anchored regex with `?` escaped, so a pattern like `**/settings` can *never* match
`/settings?from=%2Fhome`. It hangs to timeout and the test dies inside the *helper*, so the
failure looks like it is somewhere else entirely. Use a regex that admits the query, or a
trailing wildcard on any route that carries or could gain a parameter.

**A login rate limiter exhausts mid-suite.** A per-IP throttle on the auth endpoint will
starve whichever spec happens to run once the budget is gone, so which one dies is
arbitrary — **check the server log for rate-limit responses first**, before chasing the
spec. Counter-intuitively, making the suite *faster* makes it *more* throttled.

**A service worker defeats request interception.** Mocked-state screenshots silently show
stale data unless the browser context blocks service workers.

**Always point a negative test at a fixture that SHOULD trip it and watch it fail.** A
negative assertion you have never seen go red is not evidence of anything.

**A tool that finds its target by convention must be tested from where the convention
breaks.** The harness resolves the repository it works on from the caller's directory;
its own suite pinned that with an environment override, so every test ran under correct
resolution and the installed-plugin layout — a different directory, a `cd` in every
wrapper — was never exercised. Four defects shipped, each invisible in-tree and each a
clean pass about the wrong repository. **If a change touches a wrapper, a resolver, a
`cwd` argument or the wiring between a module and its CLI, drive the real entry point
from a foreign directory with the override removed.** The module's own tests, called
with the right arguments, prove nothing about the caller that omits them.

## 9. Running tests

The commands come from the stack modules your project declares. One call prints them:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/checks/check-project-config.sh    # stacks, dependency dirs, per-worker env
${CLAUDE_PLUGIN_ROOT}/harness/verify/peek.sh harness.yaml       # testing.aggregate_commands, layout, gates
```

**Run through the harness runner — it loads your per-worker resources for you.** Do not
`source .swarm-env`: every Bash call is a fresh shell, so the exports would not reach your
next command, and `source` evaluates its argument as shell code so no permission rule
matches it. Never write an inline
`VAR=value <runner> ...` form: that command string starts with `VAR=`, not the runner, so
no prefix-based permission rule can match it and the call stops on a permission prompt.
In a background subagent that prompt surfaces in the *orchestrator's* session — if nobody is
there, your dispatch hangs silently and indefinitely. This is not hypothetical; it stalled a
campaign for 8.5 hours.

**The per-worker database is still mandatory.** Where a runner reuses a database between
runs without process-level isolation, two concurrent runs against the same database name **collide destructively**. The
per-worker name is what makes a parallel backend lane possible at all.

**The whole-repo aggregates in `testing.aggregate_commands` are orchestrator wave gates, not
worker steps.** Run scoped tests for your own task. A sibling worker's in-flight edit will
otherwise produce phantom errors you waste turns chasing.

**Never run** a reseed, a full end-to-end stack, or any long-running service target,
a background worker, a production build, a dev server, or a code-generation step — they bind
singleton ports or clobber shared state. (A production build while a dev server is live corrupts
the shared build directory for both.) If your task genuinely needs one, stop and return
`NEEDS-SERIAL-LANE`.

## 10. Before you claim a task is tested

- Did I run the tests and **watch them pass** — the full relevant suite, not just mine?
- Are they genuinely adversarial, with real test data, or did I assert on nothing?
- Would each test go **red** if I deleted the implementation?
- Did I cover boundaries, empty, invalid, error and auth — not just the happy path?
- Did I avoid weakening, deleting or skipping anything to get green?
- Did lint and typecheck pass on the files I touched?

Any "no" means you are not done. Go back.
