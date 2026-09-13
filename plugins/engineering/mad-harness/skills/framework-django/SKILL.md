---
name: framework-django
description: "Django doctrine for this repository: the services boundary, migration discipline, query performance, and the settings split. Loaded ON DEMAND by an agent working in a Django lane — never preloaded, so a project not using Django pays nothing for it."
---

# Django doctrine

**Loaded on demand.** The handful of rules that are wrong most often are already in
your prompt's technology card. This is the depth behind them — read it when the card
is not enough, not routinely.

## The services boundary

**Views handle HTTP, services handle logic.** All ORM access from a view goes through
the services layer or a repository function. A view doing `.objects.filter(...)`
inline is a finding, not a style preference: it puts query logic where no other
caller can reuse it and no test can reach it without HTTP.

**Apps never import another app's models.** Cross-app traffic goes through services.
The import is the coupling — once app A imports app B's model, B cannot change its
schema without breaking A silently.

When the correct fix for your task lives in shared service code, make it, mark it
`# CORE-CHANGE(<task-id>): <why>`, and add tests at the changed call sites.

## Queries

`select_related` for forward FK/one-to-one, `prefetch_related` for reverse and M2M.
**The N+1 is the most common Django bug in review** — a loop that touches
`obj.related.field` without one of those issues one query per row, and the suite
still passes because the test data has three rows and production has thirty thousand.

Raw SQL only where the ORM genuinely cannot express the query, and always with a
comment explaining why.

## Migrations

**Forward-only once deployed. NEVER edit a landed migration.** Editing one that has
run somewhere leaves that database permanently inconsistent with the migration graph,
and nothing detects it until the next deploy fails on an unrelated change.

Schema changes are always migrations — never a hand-edited column. The order is
model → migration → service → endpoint → test, because you cannot test a service
against a model that does not exist yet.

## Settings

Split by environment. Environment variables belong in the production settings module
only; a default that silently works in dev is how a missing production variable
reaches deploy undetected.

## Testing

Per-worker database isolation is mandatory when workers run in parallel — see your
stack's card. Factories over committed fixtures: a fixture file is a snapshot that
drifts from the schema, and nothing tells you when it has.
