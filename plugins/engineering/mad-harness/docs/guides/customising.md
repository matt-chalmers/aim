# Customising

Fit the harness to your repository without forking it. Everything here is configuration or
a module; none of it edits the plugin.

## Where a fact belongs

The single most consequential decision, and the one most often got wrong.

| the fact is about | goes in | getting it wrong |
|---|---|---|
| **your repository** — where code lives, what the test command is | `harness.yaml` | — |
| **a toolchain**, true for anyone using it | a stack module | the module becomes wrong for everyone else |
| **how to write code here** | a framework module, or your own conventions file | — |
| **the harness itself** | upstream — see [contributing](contributing.md) | the harness cites a project file as external authority |

## Layout guessed wrong

```yaml
stacks:
  - name: python-uv
    root: services/api      # the module ships root: "."; the PROJECT says where it lives
```

Markers, the dependency directory and the command cwd all resolve from that one field.

## A command is wrong for your project

```yaml
stacks:
  - name: python-uv
    commands:
      test_scoped: uv run pytest {path} --no-header
```

**Overrides merge.** Naming one key leaves the module's other commands intact — replacement
semantics would mean fixing one rotted command silently dropped the five beside it.

```bash
harness/checks/check-stack-commands.sh    # probe; --repair to fix and record
```

## Concurrency is wrong for your hardware

```yaml
lanes:
  backend: { concurrency: 3 }
  frontend: { concurrency: 2 }
```

Caps live in config because a prompt cannot know your machine. Measure rather than guess:
the constraint is usually memory or a database, not CPU.

## A lens should fire on different paths

```yaml
areas:
  - path: services/billing
    triggers: [security]        # L4 fires on any diff touching this
security:
  paths: [services/auth, services/billing]
  invariants: ["a user never reads another tenant's ledger"]
```

L4 also fires on a task's `SURFACE:` line whatever the diff shows, so a task the planner
marked as touching authorization gets the security lens even when the path grep is empty.

## A tool your workers need

Do **not** pre-grant it. Let a worker ask:

1. the worker is refused, and the broker files a `Permission:` record with the command
2. you answer it from `/decision`
3. the grant lands in `permissions.allow` with the request id beside it

```yaml
permissions:
  allow:
    - Bash(curl https://pypi.org/*)   # answered PROJ-4f2a, narrowed from Bash(curl:*)
```

Grant the **narrowest** rule that unblocks the work. Every widening then carries a reason
and a record, which is the difference between a policy and a list that only grows.

## Your domain vocabulary

```yaml
domain:
  nouns: [invoice, ledger, reconciliation]
```

Guards against another project's vocabulary leaking into prompts. Choose words that do not
fire on ordinary prose — one such word cost 41 false hits in this repository before it was
removed.

## Signals and baselines

```yaml
signals:
  megafile_lines: 800       # two tasks in one file past this collide, whatever they touch
```

Baselines are **measurements of your repository**, not aspirations. A value someone hoped
for makes the contention check either useless or obstructive.

## Switching tracker backend

Changing `tracker.backend` alone leaves your records in the old store. Move them:

```bash
tk.sh migrate --to mdfiles --dry-run    # how many records would move
tk.sh migrate --to mdfiles              # then edit tracker.backend in harness.yaml
```

Ids change — each backend mints its own — so anything that quotes a task id outside the
tracker (a commit message, a doc) still names the old one. The source store is untouched,
so migrating into a clean target and comparing is safe.

## Running campaigns on more than one machine

Task claims and the merge slot live in `.harness/run/`, which is local to a checkout, so
they do not coordinate anything between machines. Take an epic lease before starting:

```bash
tk.sh lease list                # what other machines hold
tk.sh lease acquire <epic-id>   # exit 1 if held elsewhere
tk.sh lease release <epic-id>   # at close-out
```

`/campaign` does this for you — the lease list joins the gate list in its exclusion set. A
machine that crashes leaves its lease; `tk.sh lease steal <epic-id>` reclaims one past its
TTL and refuses while it is still fresh.

## Project-local module overrides

`harness.yaml` and the stack modules may be shadowed by project-local copies under
`.harness/`. Use this for something genuinely local; anything reusable belongs upstream, or
the next project repeats the work.

## After any change

```bash
harness/checks/check-project-config.sh
harness/checks/check-stack-commands.sh
harness/checks/stack-card.sh <lane>        # what a dispatch in this lane will now carry
```

Run the second after any dependency change. It catches a rotted command at pre-flight
rather than inside a worker's worktree mid-wave, where the escalation policy reads it as
the worker's fault rather than the config's.

## What you cannot configure away

| | why |
|---|---|
| the sandbox requirement | workers merge unattended; containment is not optional |
| L3 never seeing the diff | it is what makes agreement between lenses meaningful |
| one gate on the merged tree | a per-branch gate cannot see an interaction defect |
| the orchestrator owning the tracked export | eight writers produce eight conflicting versions |
