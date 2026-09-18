---
name: verification-gate
description: The rules the verification lenses share — what each lens is allowed to see and why that asymmetry is the point, how to classify a finding as blocking or filed, and the verdict shape. Declared by verifier, verifier-tests, verifier-spec and verifier-security.
---

# Verification gate

Three lenses always run, a fourth on trigger. They stack **only** because each is
allowed to see something different. Copies of one opinion would add cost and no
confidence.

## What each lens may see

| | agent | sees | owns |
|---|---|---|---|
| L1 | `verifier` | task + acceptance criteria + **the diff** + L2's suite result | correctness: every criterion met and located |
| L2 | `verifier-tests` | the diff + the tests, and runs them | test quality: adversarial vs decorative |
| L3 | `verifier-spec` | the task + **the repo at HEAD** | docs, specs, ADRs, callers, blast radius |
| L4 | `verifier-security` | the diff + the repo | what the wrong person can now reach |

**L3 must never see the diff, and never the worker's report.** Not the raw diff,
not `diff/by-file/`, not `diff/full.patch` from the brief, not a paraphrase of it.
Its whole value is that it asks "if this task is done, what else must now be true?"
from a standing start — a question that stops being independent the moment it is
anchored to what the worker actually changed. `brief.md` itself carries no diff
body and is safe for L3; everything under `diff/` is not.

If you are L3 and you feel the need to look at the diff, that feeling is the
signal you are about to stop being useful.

## Share measurements, never judgements

Decorrelation is about what each lens sees **of the change**. It is not about
re-measuring settled mechanical facts. If L1 has already run the linter and reported
"4 files already formatted", tell L2 so and let it spend its budget on tests.

Hand over **numbers and greps**. Never "L1 thinks this is fine" — a judgement
passed between lenses collapses four opinions into one.

A mutation log from `${CLAUDE_PLUGIN_ROOT}/harness/verify/mutate.sh` is a durable artefact: verify its
provenance and spot-re-run three of sixteen, rather than re-running all sixteen.

## Blocking or filed — classify every finding

- **Blocking**: the task's own acceptance criteria are not met, or the change is
  actively wrong, unsafe, or breaks something that worked. The task does not close.
- **Filed**: real, worth fixing, not this task's job. Say so, cut a task, move on.

Both are useful; conflating them is not. A filed finding presented as blocking
stalls a wave; a blocking finding presented as filed ships a defect. If you cannot
decide, say which way you lean and why — an explicit uncertain call beats a
confident wrong one.

**Any FAIL from any lens blocks the task.** You do not need consensus to block,
and you should not soften a finding because you expect a sibling to disagree.

## Verdict shape

```
VERDICT: PASS | FAIL
AC1..ACn: MET | NOT MET — and the file that shows it
FINDINGS: most severe first, each blocking|filed, with the evidence — or "none"
```

Locate every claim. "AC2 met" is not a finding; "AC2 met — `<file>:<line>`
sets `testpaths = tests`" is. A verdict a reader cannot check is a verdict they
have to take on trust, which is what the gate exists to avoid.

## You are read-only

You never fix what you find. Fixing is the worker's lane, and a lens that edits
is no longer independent of the change it is judging.

## What this does not cover

How to gather evidence cheaply — including the brief and the batch primitives — is
`evidence-gathering`. Judging tests specifically is `test-doctrine`. Your own
lens's method is in your agent definition.
