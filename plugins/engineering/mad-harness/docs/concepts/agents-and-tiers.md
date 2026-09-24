# Agents and tiers

Every agent declares what it is, and the harness derives the rest — which model, which
ceiling, which doctrine. Nothing keys off a list of agent names, because a name list goes stale
the first time an agent changes shape.

## The tiers

These are the plugin's **defaults**. A consuming project patches any of them, adds tiers, and replaces the ladder in its own `harness.yaml` — see [harness.yaml](../reference/harness-yaml.md); every dispatch record then says `tier_source: project`.

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
3. **project override** — `agent_tiers:` in `harness.yaml`, agent → tier. The switch for moving a
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

## A stage can run below its agent's tier

The tiers above are what an agent declares it *needs*. For the planning stages of an epic,
the harness asks a second question first: how much of that does **this** epic actually
require?

`plan_tiers` (on by default since 0.10.29) reads a **complexity card** — computed from the
epic's own record, its children and the project's declared surface — and may run a stage
lower than the agent's declared tier:

| stage | agent | declared | runs at | when |
|---|---|---|---|---|
| survey | `analyst-survey` | `worker` | unchanged | — |
| design / sanity-check | `architect` | `strategic` | **`strong`** | the surface is not FLAGGED |
| plan | `planner` | `strong` | unchanged | — the planner writes the DAG |
| audit | `analyst` | `strong` | **`worker`** | the surface reads SIMPLE |

Two things make this safe rather than merely cheap:

**The agent is told, and can refuse.** Every demoted stage carries a `TIER:` line naming the
reading that demoted it and instructing the agent to answer `ADEQUACY: ESCALATE — <why>` (or
`VERDICT: ESCALATE`) and stop if what it finds needs deeper deliberation. The stage is then
re-run at its full tier with that reason recorded. The judgement about whether a cheap tier
was adequate is made by something that has read the actual epic, not by a heuristic in front
of it.

**The planner never moves.** It produces the dependency graph every later stage and every
worker is scheduled from; being wrong there is expensive in a way no lens recovers.

Measured at −33% cost per §3 with the spreads separate. What is not yet measured, and is
stated as such: the audit's catch rate at `worker`, and how often the escalation path fires.

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

It is a **circuit breaker per dispatch**, not a target and not a hard cap — and *who checks
it* depends on whether the harness can price the tier: the SDK on Anthropic, the harness
itself on a tier that declares a price. Both the mechanism and its three deliberate
non-guarantees are in [cost](cost.md#ceilings).

The figure the SDK reports is a client-side estimate, and off Anthropic it is an estimate of
the wrong thing — see [providers](providers.md).

See also: [reference/agents.md](../reference/agents.md) for the full roster.
