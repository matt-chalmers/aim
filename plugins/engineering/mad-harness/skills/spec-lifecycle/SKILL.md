---
name: spec-lifecycle
description: How a requirement becomes a spec and then code — what the staging directory holds, the two fold-in moments, and the rule that specs are allowed to lead the code. Preloaded by analyst and analyst-survey (which judge and survey requirements), spec-editor (which applies them) and architect (which designs against them).
---

# Spec lifecycle

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

## The rule the corpus rests on

**Specs describe the target system. Build frameworks that grow into them.**

An unimplemented spec'd feature is a **visible, honest gap** between the docs and
the code. Do **not** "reconcile" that gap by dumbing a doc down to match what is
built — that hides the target. The tech debt to avoid is the opposite: shipping an
underlying framework or data model as a shortcut that *cannot* support the specced
future, so it needs rework to deliver what the spec already describes.

If you ship a smaller first step, make it a genuine **stepping-stone** that extends
cleanly into the target — never a dead-end — and record the remaining gap as a task
with a concrete trigger and cost. **Close a spec↔code gap by implementing toward
the spec, never by deleting the spec.** If a spec is genuinely wrong or outdated,
that is a design change: flag it for the owner rather than silently rewriting it.

## What `<paths.proposed>/<epic>-<slug>/` stages

`<epic>` here is the **bare** id — the part after the project prefix, `m7j7` for
`PROJ-m7j7` — which is how every staged folder is named on disk. Every harness script that
looks a folder up (`spec-index-status.sh`, `check-decision-register.sh`, `archive-epic.sh`,
`render-epic.sh`) accepts either form of the id and says what it tried when it finds nothing;
the two forms are never a reason for a silent miss.

Nothing here is a claim about the running system, and **nothing survives its own
fold-in**:

| file | is |
|---|---|
| `proposal.md` | the requirements the epic must satisfy |
| `adr-<slug>.md` | one per outstanding decision. **Never cite a draft ADR as settled.** |
| `design.md` | the architect's solution design |
| `spec-index.md` | the survey's pointer map — a **regenerated cache, never a source** |
| `decisions.md` | the decision register: frontmatter is the register, not a table |
| `run-log.md` | wave and campaign narrative. **Ephemeral** — the epic record is not a log |
| `tasks.md` | **GENERATED** view of the plan and its state — never hand-edited, regenerated at each wave boundary |

## The two fold-in moments

- **Fold-in ① at epic start** — the proposal lands in the feature docs as
  **unchecked acceptance criteria**. The build then makes them true.
- **Fold-in ② at epic close** — the design lands as an **ADR plus architecture
  docs**, routed by the design's own `## Fold-in routing` table. The spent folder is then
  **retired**: `${CLAUDE_PLUGIN_ROOT}/harness/checks/archive-epic.sh <epic>` moves it under
  `paths.archive`, dated and stamped, when the project declares one — otherwise it is
  deleted, as before.

**Do not stamp what receives the content.** An earlier version of this skill asked for a
`<!-- folded-in: ABC-123 @ sha -->` comment in every doc a fold-in touched. Git already
answers both directions, per line, and cannot drift:

```bash
git blame <doc>                          # this line -> the commit -> the id in its subject
git log --grep=<epic-id> --name-only     # this epic -> every doc it touched
```

Both work because commit subjects carry the id already. The comment added a line per
fold-in per doc — unbounded growth in the files people actually read — and pinned a `sha`,
which is the same species of ageing pointer that `check-line-pins.sh` exists to catch.

What the archive owes is that the folder survives with its date and epic in the path. The
derivation is git's job.

**An archived folder is WRITE-ONCE.** A stale *plan* document is dangerous because it
makes present-tense claims about the system; an archived proposal claims only *"this is
what epic X proposed on this date"*, which stays true forever. Snapshots cannot rot. Edit
one and it becomes a competing source of truth again — which is what deleting them was
avoiding in the first place.

Both are `spec-editor`'s lane. It applies an approved proposal and nothing else:
it never writes code, never invents requirements, and never judges whether the
proposal was any good — that is the analyst's lens, and keeping the two apart is
the point.

## The mechanical gates

```bash
${CLAUDE_PLUGIN_ROOT}/harness/checks/spec-index-status.sh <epic-id>      # reuse / delta / rebuild — a diff, not a judgement
${CLAUDE_PLUGIN_ROOT}/harness/checks/check-decision-register.sh <epic>   # register vs the tracker, and cross-proposal contention
${CLAUDE_PLUGIN_ROOT}/harness/checks/check-doc-drift.sh <base> [head]    # one statement of a rule amended, the others left
```

`check-doc-drift.sh` exists because the most common lens FAIL was a change that
edited one statement of a rule and left every other statement untouched — turning a
file that was consistently incomplete into one that is self-contradictory. **A
half-applied doc correction is worse than none.**

the register owns the epic↔decision association; the tracker owns status. A settled
decision must not sit in `open` blocking an epic, and a still-open one must not
hide in `settled` and silently unblock a fold-in.

## What this does not cover

The full lifecycle reference, if your project keeps one, lives under `paths.architecture`. Where any
given feature is specified is the corpus index (`harness.yaml` → `paths.index`).
