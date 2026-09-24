# Verification

Four lenses judge completed work independently. The design rests on one idea that is easy
to state and easy to break: **decorrelation**. Lenses reading the same evidence make the
same mistakes, so their agreement proves nothing. Each is therefore given a deliberately
different view, and the enforcement of that difference is structural rather than advisory.

<img src="../assets/lens-evidence.svg" alt="The brief splits into body and diff; L3 receives the body and the repository but never the diff">

| lens | agent | judges | evidence |
|---|---|---|---|
| L1 | `verifier` | correctness against acceptance criteria | brief body + `diff/` |
| L2 | `verifier-tests` | would these tests catch a regression | brief body + `diff/` + suite |
| L3 | `verifier-spec` | docs, callers, blast radius | brief body + **repo at HEAD** |
| L4 | `verifier-security` | what a wrong actor could do | fires on an `areas[].triggers` match |

## Why L3 must not see the diff

This is the rule most likely to be "helpfully" broken by someone tidying up, so the reason
matters more than the rule.

A lens shown the diff judges *the change*. L3's job is to judge **what the repository now
claims** — whether a docstring still tells the truth, whether a caller still holds, whether
two documents now disagree. Given the diff it starts reviewing the patch like the others,
and four lenses collapse into one opinion held four times.

The separation is physical, not instructional — since 0.10.21, when it became so. Before,
`brief.py` wrote the diff *under* the brief root, `brief.md` printed those paths in a
section every lens read, and every reader was granted the directory: the rule was a
request in the very file that said where the diff was. Now `brief.md` carries no pointer,
the diff lives in a sibling root, `verifier-spec` declares `evidence: no-diff` and is
denied that root on the dispatch, and `lens-gate.sh` asserts L3's prompt names no such
path.

Two runs from this repository's own test lab:

- L1 read the patches and passed a task. L3 reasoned from the repository and failed it,
  having found `(+61) 400 000 000` silently losing its international prefix.
- Both failed a later task after L3 hand-traced `"\t+61 400"` through code whose docstring
  promised whitespace handling it did not implement.

Neither finding is visible in a diff. Both are visible in the repository.

## The brief

```bash
harness/verify/brief.sh <task-id> [commit-ish] [--out DIR]
# stdout: path to brief.md
# stderr: size comparison and notes
```

Computed once and handed to every lens, so four dispatches do not each re-read the
repository. The streams are separate for a reason worth knowing: stdout is block-buffered
to a file while stderr is not, so merging them and taking the first line yields the size
note rather than the path.

```
<root>/<task>-<sha>/
├── brief.md        task, criteria, files changed, worker notes   [safe for L3]
└── diff/
    ├── <file>.patch                                              [L1, L2, L4 only]
    └── full.patch
```

Default root is `.harness/run/briefs/` **inside the project** — scratch, gitignored, and a
lens's own working directory, so no grant is involved. Briefs followed `SCRATCHPAD`/`TMPDIR`
once and the lens was handed that directory through `add_dirs`; the moment the brief's
writer and the lens's dispatcher ran in different environments (a sandbox sets its own
`TMPDIR`), every lens in a headless epic was denied `Read` on its own brief.

## Running the gate

One call does the whole of it, so the orchestrator never assembles four dispatches by hand
and never reads four verdicts by eye:

```bash
harness/swarm/lens-gate.sh <task> <sha> [--branch <b>] [--json]
# exit 0 PASS · 1 FAIL, with the route printed · 2 could not judge
```

It builds the brief, decides whether L4 fires, runs the suite once where the change actually
is, dispatches the lenses concurrently, parses the verdicts, applies unanimity, and writes
the `VERIFIED <sha>` note on the task — **only** when every lens passed.

## Three states, and the one that is not a pass

A lens returns `PASS`, `FAIL`, or **`NONE` — could not judge**. That third state is the one
that matters, and it is never treated as a pass.

A lens that hung, was denied a tool it needed, was cut off by its ceiling, or answered
without a labelled verdict has produced no judgement. Reading that as approval is how a gate
quietly stops being one — and it is not hypothetical: a lens that could not read the brief
written for it once returned `VERDICT: PASS` anyway, which is why a verdict is parsed rather
than inferred and why the word `PASS` in prose does not count.

**Unanimity.** Any FAIL from any lens blocks the task. A boolean, not a judgement.

**One parser, and it has been wrong.** Because a missing verdict is a blocked task, how the
verdict line is *spelled* is a real operational risk. Models write `VERDICT: PASS`,
`**VERDICT:** PASS`, `## VERDICT: PASS` and `` `VERDICT: PASS` `` — and each formatting it
did not know cost a discarded judgement and a task blocked by lenses that had passed it,
found twice by re-reading the answers a lab run kept on disk. Markdown around the word is
formatting; the verdict is the word after it.

## What a FAIL means

A FAIL is not a veto on shipping. It is a finding that must be **filed before the push** —
the gate's value is that a finding cannot be silently dropped, not that it halts the world.
Routing is part of the verdict: a test-shaped finding goes to `quality-engineer`, a
code-shaped one to `fullstack-engineer`.

Verdicts vary between runs on genuinely ambiguous criteria. Treat that as information about
the criterion rather than about the lens: a criterion two careful readers can disagree
about is a spec defect, and `analyst-survey` exists to catch those before any code is
written. The fix is a worked example in the acceptance criteria, not a stricter lens.

## Related doctrine

| skill | covers |
|---|---|
| [`verification-gate`](../../skills/verification-gate/SKILL.md) | what each lens may see, and the routing rules |
| [`test-doctrine`](../../skills/test-doctrine/SKILL.md) | what "tested" means here — what L2 judges against |
| [`evidence-gathering`](../../skills/evidence-gathering/SKILL.md) | finding what is true without exhausting the budget |
