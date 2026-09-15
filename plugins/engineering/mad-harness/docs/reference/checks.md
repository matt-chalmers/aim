# Checks

Twenty mechanical gates in [`harness/checks/`](../../harness/checks/). Each enforces a rule
that would otherwise be a convention people drift from.

**Every guard carries a companion test proving it can fail.** A check that cannot fail is
not a guard, it is decoration — and this corpus has caught itself shipping three.

<!-- GENERATED:checks — do not hand-edit; run harness/checks/check-docs.sh --write -->

| check | enforces |
|---|---|
| `archive-epic.sh` | Retire a spent epic's staging folder by MOVING it into paths.archive, stamped and dated. |
| `check-analyst-mirror.sh` | The analyst is split in two so the SURVEY can run at lower effort than the AUDIT: |
| `check-blocking-prose.sh` | Find records whose TEXT claims a blocking relationship that has no dependency EDGE. |
| `check-conventions-mirror.sh` | The harness conventions block is stated in three skills so that every agent reaches it |
| `check-decision-register.sh` | Verify a staged epic's decision register against the tracker, and check cross-proposal |
| `check-doc-drift.sh` | check-doc-drift.sh <base-ref> [head-ref] |
| `check-docs.sh` | Verify the documentation's generated parts against the code they describe. |
| `check-line-pins.sh` | Which records cite code by line number, and which of those pins have drifted. |
| `check-model-config.sh` | Verify the model routing config against every agent definition. |
| `check-ports.sh` | Which of the project's declared ports are already bound, and by what. |
| `check-project-config.sh` | Verify harness.yaml + harness/stacks/, and that the places still |
| `check-prose.sh` | Catch prose an edit broke: a sentence whose subject was deleted, a rule stated twice, |
| `check-record-size.sh` | Warn before a tracker record hits the ceiling that write-locks it. |
| `check-script-refs.sh` | Every `${CLAUDE_PLUGIN_ROOT}/...` path named in a skill, command or agent must exist. |
| `check-skills.sh` | Verify the skill layer: declarations resolve, names match, and no agent's |
| `check-stack-commands.sh` | Probe each stack's declared commands, and repair the ones that have rotted. |
| `domain-report.sh` | Where the harness's prose uses a consuming project's domain vocabulary. |
| `gating-ratio.sh` | How much of the backlog is buildable at all — open records, how many are decisions or |
| `spec-index-status.sh` | Answer campaign-loop §3a's reuse / delta / rebuild decision mechanically. |
| `stack-card.sh` | Print the technology card for a lane. |

<!-- /GENERATED:checks -->

## Running them

```bash
make check     # everything: lint, suite, project, skills, models, prose, commands
```

Individually, each takes no arguments and prints one line when healthy. That matters:
a check that cries wolf gets ignored, so a passing check says almost nothing and a failing
one says exactly what broke.

## `gating-ratio.sh` is worth watching

It reports open records against how many are decisions. A backlog that is mostly decisions
is not a pipeline problem — it is waiting on you. Since `Permission:` records share that
queue, a rising share of them is the signal that the [permission
model](../concepts/permissions.md) needs changing rather than more approvals.
