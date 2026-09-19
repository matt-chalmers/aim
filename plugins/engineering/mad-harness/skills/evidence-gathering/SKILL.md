---
name: evidence-gathering
description: 'How to find out what is true about this repository without burning the budget doing it — the measured cost model, the precomputed lens brief, and the batch primitives that answer many questions in one tool call. Declared by every agent whose job is to READ the repo and report — the four verification lenses, fidelity-auditor, analyst, analyst-survey, planner and architect — and, measured to pay for itself, by the two writers; delivered to a dispatched agent by the harness, in its system prompt, not by the CLI.'
---

# Evidence gathering

Your findings are only as good as what you looked at, and looking is what you
spend. This is how to look at everything you need to and still have budget left.

<!-- HARNESS CONVENTIONS: mirrored in evidence-gathering and spec-lifecycle, and
     verified byte-identical by check-conventions-mirror.sh. Edit one and the check
     fails; edit both or neither. -->

## Harness conventions

These are the harness's own rules about the artefacts the harness owns — task text, lens
reports and return lines. They are stated here rather than cited from a project file
because the harness defines them and its own checks enforce them.

**Pair every task id with a short gloss.** `PROJ-4f2a` tells a reader nothing; they
have to look it up to follow the sentence. Write `PROJ-4f2a (retry budget on the ingest job)` —
six words maximum, ideally three or four. It is a handle, not a summary. Use the same
gloss for the same task all session, so a reader can track it across a wave. The bare id
is correct in commit messages and in the tracker’s own arguments, where it is the identifier.

**Cite code by symbol, never by line number**, in anything persisted to a task. Write
`services/billing.py::recompute_invoice_total`, not a line. A symbol survives edits above
it; a line number survives none of them, and a stale pin that lands on plausible-looking
wrong text is worse than one that obviously misses. For prose, quote the opening words
instead — quoted text is greppable. Line numbers are fine in a lens report or in chat,
which is what `peek.sh` emits them for; the ban is on what gets written down.

<!-- END HARNESS CONVENTIONS -->

## Tool calls are the cost, not prompt size

Measured across 24 lens dispatches in one campaign:

```
tokens ≈ 18,700 + 2,600 × tool_calls
```

Two things follow, and both are counter-intuitive:

- **Trimming your prompt saves almost nothing.** Tokens-per-call *falls* as calls
  rise (3,397 at ≤30 calls, 2,825 at 45+) because the accumulated conversation is
  served from cache. A shorter prompt is a *clearer* one; it is not a cheaper one.
- **Halving your tool calls is worth ~44%.** That is the lever. Every question you
  can ask in the same call as another question, ask in the same call.

Four real lens runs were mined for every command they issued — 92 bash calls, of
which **58 asked one small question each**: 31 searches, 16 slice reads, 11 reads
at a revision. Those are what the primitives below collapse.

## Where the harness scripts are

`${CLAUDE_PLUGIN_ROOT}/harness/...`, as written throughout this file. The plugin loader
substitutes the real install path before you ever see the text, so what reaches you is
already absolute and needs no resolving.

**Write it that way and nothing else.** Three spellings that look equivalent are not:

| what you write | what happens |
|---|---|
| `${CLAUDE_PLUGIN_ROOT}/harness/…` | expanded at load; permitted; runs |
| `$HARNESS_ROOT/…` | a SHELL variable. The dispatcher sets it, an interactive session does not — and no permission rule can match a command naming a variable, because matching is textual |
| `harness/…` | resolves only when the harness happens to sit inside the repository you are working on, which it usually does not |

Measured, in frontmatter and on the command line alike. The middle row cost this harness
every interactive invocation of every command until it was found.

## Never `git show <sha>`

Measured on a 44-file commit: `git show` is **~96,000 tokens**. That is most of a
dispatch, spent in one call, on content you will use a fraction of.

The orchestrator should hand you a **brief** built by `${CLAUDE_PLUGIN_ROOT}/harness/verify/brief.sh`:

```
brief.md              measurements only — scope, changed paths by area, diff stat,
                      the task text. ~1,700 tokens instead of ~96,000.
diff/stat.txt         the --stat table
diff/files.txt        changed paths, one per line
diff/by-file/<slug>.patch   ONE file's diff  (slug = path with non-alphanumerics → '-')
diff/full.patch       the whole diff, on disk. Reading it costs the entire saving.
```

Read `diff/by-file/` for the files your lens actually reasons about. If you were
not given a brief and you need one, build it — it is one call.

**Whether you may read `diff/` at all depends on which lens you are.** That is a
correctness rule, not a budget one; see the `verification-gate` skill.

## Batch your questions

```bash
${CLAUDE_PLUGIN_ROOT}/harness/verify/scan.sh -e 'PATTERN' -e 'PATTERN' -e 'PATTERN' [--rev SHA] [pathspec...]
${CLAUDE_PLUGIN_ROOT}/harness/verify/peek.sh path:10-40 other/file.py:1-25 third.md [--rev SHA]
```

Before you start, write down every search you expect to run and every file you
already know you want. Then issue **one** `scan.sh` and **one** `peek.sh`. Going
back for a second batch when the first answers raise a new question is fine and
expected — what wastes budget is asking eight questions in eight calls when you
knew all eight at the start.

`peek.sh` takes `path`, `path:START`, or `path:START-END`, and `--rev` reads at a
commit, so it replaces `sed -n`, `head`, `cat` **and** `git show <sha>:<path>`.

## Batch your RUNS too

The three primitives above batch **reads**, which is why they are toolchain-neutral —
underneath they are `git grep`, `git show` and `git diff`. Running things is the other
half, and it has its own primitive:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/verify/run.sh --stack <name> lint typecheck test
${CLAUDE_PLUGIN_ROOT}/harness/verify/run.sh --lane backend --scoped path/to/tests test_scoped
```

It resolves each key against the stack's config, so you never have to work out which
runner this project uses, nor which directory to run it in. The same rule holds
as for `scan` and `peek`: **every key you name produces a line**, and one the stack does
not declare comes back as `--` rather than vanishing.

## A result is paid on every later turn — window it

Every later turn re-reads a tool result, so its weight is *size × turns remaining*. Two
field workers carried **28% of everything they read** as their own results — not test
output, which gets `| tail`ed by reflex, but whole files read in one call (38k, 54k
chars) and `grep -A 400` on one document, three times for the same section.

- **Read in windows.** `peek.sh path:START-END` after `scan.sh` finds the region; never
  a whole file over ~200 lines, never `cat`. On SWE-bench Lite a 100-line window
  resolved 5.3 points *more* than whole-file reads — stale content misleads as well as costs.
- **Long output goes to a file first** (`cmd > .harness/run/out/x.log 2>&1`, then
  `tail`/`grep`/`peek.sh`). The CLI keeps the first 30,000 chars of a bash result and
  drops the rest with no path — for a suite, that is the summary. `run.sh` already keeps
  each command's whole log and reports the failures named plus the path.

## Trust a zero result — it is an answer, not a gap

Both primitives answer **every** input. A pattern that matched nothing reports
`NO MATCHES — searched and found nothing`; a path that does not exist reports
`!! not found`; the summary names every spec that failed.

This matters because the most valuable answer a lens gets is usually negative —
"no stale reference remains anywhere tracked" *is* `hits=0`. Do not re-run a
pattern individually to confirm a zero; the batch already confirmed it. Re-running
to double-check is the single easiest way to give back the saving.

The match **listing** is capped; the **count** is exact. `hits=47` with twelve
lines shown means forty-seven, not "at least twelve".

## What this does not cover

Running tests and judging them is `test-doctrine`. The worktree you run in is
`worker-protocol`. Which evidence each lens is *allowed* to see is
`verification-gate`. The full script map is `harness/README.md`.
