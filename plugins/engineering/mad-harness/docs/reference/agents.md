# Agents

Every agent in [`agents/`](../../agents/). Each is a markdown file: frontmatter declares
what it is, the body is its prompt — and an **activity** decides what runs it, which is why
no agent names a model or a strength.

## Frontmatter contract

| key | means |
|---|---|
| `description` | when the orchestrator should reach for it |
| `tools` | the tool set. **Containing `Edit` or `Write` makes it a writer** — that is how the harness classifies it, not a name list |
| `skills` | the doctrine the agent declares — delivered, in full, as part of its system prompt on every dispatch (the CLI does not preload frontmatter skills on the `--agent` path, so the harness does); a declared skill that cannot be found refuses the dispatch |
| `isolation` | `worktree` means it must never run in the primary checkout — dispatch refuses |

Everything the harness decides about an agent derives from those fields. A name list would
go stale the first time an agent changed shape.

`model_tier` was here until 0.12.0. An agent no longer picks what runs it: the
[activity](../concepts/agents-and-activities.md) it performs does, so one agent can serve two
activities at two strengths — the architect's sanity-check and its full design. `model:` and
`effort:` are still present and still generated, for the native `claude --agent` path only.

## The roster

<!-- GENERATED:agents — do not hand-edit; run harness/checks/check-docs.sh --write -->

| agent | performs | declares |
|---|---|---|
| `analyst-survey` | `spec.survey` | reader |
| `analyst` | `spec.audit`, `plan.audit` | reader |
| `architect` | `design.sanity-check`, `design.create` | reader |
| `campaign-orchestrator` | `loop.orchestrate` | writer |
| `fidelity-auditor` | `work.fidelity` | reader |
| `fullstack-engineer` | `work.implement` | writer · worktree |
| `planner` | `plan.create` | reader |
| `quality-engineer` | `work.remediate` | writer · worktree |
| `spec-editor` | `spec.draft`, `spec.fold-in` | writer |
| `verifier-security` | `verify.security` | reader |
| `verifier-spec` | `verify.spec` | reader |
| `verifier-tests` | `verify.tests` | reader |
| `verifier` | `verify.impl` | reader |

<!-- /GENERATED:agents -->

The analyst is deliberately **two files**: the SURVEY runs on every epic and the AUDIT only
on drafted specs, so the cheap half runs cheap. `check-analyst-mirror.sh` keeps the shared
half identical.

## What an agent must not contain

**No technology names.** An agent that names a toolchain cannot serve a project using a
different one, and the failure is not an error — it is confident wrong advice. Technology
reaches an agent through its lane's **card**, injected at dispatch.
`test_no_agent_names_a_technology` enforces this.

**No line-number citations.** Cite code by symbol — `file.py::function`. A symbol survives
edits above it; a line number does not. `check-line-pins.sh` enforces it.

## Adding one

See [guides/contributing.md](../guides/contributing.md).
