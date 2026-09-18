# Agents and tiers

Twelve agents and three tiers, on one rule: an agent declares what it is, and the
harness derives the rest. Nothing keys off a list of agent names, because a name list goes stale
the first time an agent changes shape.

## The tiers

| tier | model | effort | ceiling | for |
|---|---|---|---|---|
| `worker` | `claude-sonnet-5` | high | $3.00, and a told budget of 400k tokens | well-specified work with a cheap recovery path |
| `strong` | `claude-opus-5[1m]` | xhigh | $4.00 | reasoning over a codebase, where being wrong is expensive to detect |
| `strategic` | `claude-opus-5[1m]` | max | $8.00 | the work that decides what everything else builds against |

Defined once in [`harness/models/tiers.yaml`](../../harness/models/tiers.yaml). An agent
names a tier; it never names a model. The worker's `task_budget_tokens` is the one figure
that moved on a measurement — a worker told its budget paces, −32% per run with the
spreads apart — and a project raises it in `harness.yaml` (`dispatch.task_budget_tokens`)
when its tasks carry more to read than the lab's.

## How a tier is chosen

Five sources, first match wins. `Resolved.reason` records which one applied, so a surprising
bill can be traced to a decision rather than guessed at.

1. **explicit override** — an operator or an escalation said so outright
2. **policy** — high-risk work is forced up regardless of what follows; a project override
   can never lower it
3. **project override** — `tiers:` in `harness.yaml`, agent → tier. The switch for moving a
   lens between tiers without patching the plugin — `verifier-spec: worker`, say — and a
   switch rather than a default because a verification gate's catch rate is measured in
   the field before its tier moves for everyone
4. **agent default** — `model_tier:` in the agent's own frontmatter
5. **global default** — `default_tier:` in `tiers.yaml`

`check-model-config.sh` fails the build if an agent names a tier that does not exist, so
the two cannot drift; it judges the plugin's defaults, not a project's overrides.

## The agents

**Verification — four lenses, deliberately decorrelated** ([verification](verification.md))

| agent | judges | sees |
|---|---|---|
| `verifier` | correctness | the brief and the per-file patches |
| `verifier-tests` | test quality | the diff and the suite |
| `verifier-spec` | spec, docs, blast radius | the brief body and the repo — **never the diff** |
| `verifier-security` | what a wrong actor could do | fires on trigger, not every task |

**Requirements — one role, two modes, so the cheap half runs cheap**

| agent | mode |
|---|---|
| `analyst-survey` | SURVEY — is this specified well enough to design against? |
| `analyst` | AUDIT — does the drafted spec hold up? |

**Producing work**

| agent | produces |
|---|---|
| `architect` | the technical approach, before any code |
| `planner` | a reviewable task DAG |
| `spec-editor` | applies an approved proposal to the durable docs |
| `fullstack-engineer` | the implementation, in an isolated worktree |
| `quality-engineer` | hardened suites |
| `fidelity-auditor` | a screen against its design handover |

## Cost is measured, not argued

Routing decisions are only as good as the data behind them, so every dispatch appends its
actual cost to a series rather than relying on an estimate.

Every dispatch appends cost, tokens, turns and denials to a series that `models/report.py`
reads back. That replaced an estimate — `tokens ≈ 18,700 + 2,600 × tool_calls` — with
observation, which is the point of routing through a boundary at all.

```bash
make models-cost     # per agent and tier: count, cost, turns, fail%, escalations, budget kills,
                     #   cache hit and write %, results% (own tool results re-read), cache breaks
```

What each column means, which levers exist and what each one measured, and where a
campaign's money actually goes: [cost](cost.md).

## What `max_budget_usd` actually does

It is passed to the SDK, so **Claude Code enforces it, not the harness.** When it trips the
dispatch ends with `error_max_budget_usd`; the run's transcript so far, its cost and its
turns are kept and recorded (`terminal: budget`), `Outcome.ok` is false, and `dispatch.sh`
exits 3 so the swarm can route it. It is the ceiling the model never sees;
`task_budget_tokens` is the budget it is *told*, and that is the one that changed the number.

Three things it deliberately does **not** do:

**It is not a hard cap.** The budget is checked *between* API calls, so one expensive call
can carry a dispatch past it. Measured: a `$0.005` budget produced a `$0.1118` dispatch —
22× over. It reliably stops a runaway *loop*; it does not bound a single large call. Set
these to "obviously too much for this tier's work", not to a number you intend to hold
anyone to.

**It does not trigger escalation.** Budget exhaustion means the work exceeded its ceiling,
not that the model was too weak. Re-running on a costlier tier turns a visible limit into a
bigger bill. Raise the ceiling or split the task.

**It does not roll anything back.** A writer stopped mid-task leaves partial edits in its
worktree. They are uncommitted and isolated, and `worktree-sweep.sh` reports them rather
than discarding them — but the task is not done, whatever the worktree contains.

The figure itself is a **client-side estimate**, not billing data.

See also: [reference/agents.md](../reference/agents.md) for the full roster.
