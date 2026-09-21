# Cost

Every dispatch records what it cost; every default that touches cost moved on a
measurement, and the measurement is written down beside it. This page is the map: what is
recorded, how to read it back, which levers exist and what each one measured, and the
rules that decide where the money goes.

## What a dispatch records

`models/dispatch.py::Outcome.telemetry` appends one event per dispatch through the
telemetry port. Beside the identity fields (`agent`, `tier`, `reason`, `model`, `effort`,
`max_budget_usd`, `task_budget_tokens`, `task`, `attempt`, `escalated_from`):

| field | is |
|---|---|
| `cost_usd`, `turns`, `duration_ms` | the SDK's own accounting — a client-side estimate, not billing |
| `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens` | the four token classes |
| `cache_hit_pct`, `cache_write_pct` | shares of everything the dispatch sent; a cold start shows low hit, a resumed agent ~0 |
| `terminal` | why it ended — `success`, `budget`, `max_turns`, `usage_limit`, `api_error`, `error` |
| `ok`, `permission_denials`, `denied_tools` | a denial is not-ok even when the CLI calls it success — see [permissions](permissions.md) |
| `experiment`, `levers` | the A/B arm this ran under, and every lever's value — so a series can always be read back |
| `tool_results`, `tool_result_chars`, `large_results`, `carried_result_tokens`, `result_chars_by_tool` | what the agent's own tool results cost it, read from its transcript: a result is re-read on every later turn, so its weight is size × turns remaining |
| `cache_breaks`, `cache_break_reasons`, `rewritten_tokens` | requests whose cache read fell short of what the previous one had cached, by the gap that explains it — `ttl_5m`, `ttl_1h`, `mutation` |

The transcript-derived fields are absent, not zero, when the session transcript is not
found. `env_names` carries variable names and never values.

## Reading it back

```bash
make models-cost                         # per agent and tier: n, cost, turns, fail%, escalations,
                                         #   kills, cache%, write%, results%, large, breaks
harness/wavelab/ab-report.sh <lever>     # an A/B series: medians, IQRs, whether the spreads separate
harness/checks/session-cost.sh <id>      # one session's context curve, in tokens — the orchestrator's side
```

`results%` is the share of a tier's prompt that was its own tool results. Two field workers
ran at 28%; the lab's run at ~7%. `breaks` counts prefix re-writes: in one field
orchestrator session, four idle gaps of over an hour each made the next request re-write
its 700–920k-token context at the write rate — 3.5M tokens, plus a 700k mutation break —
more than its campaign's whole orchestrator spend, and the largest single cost in either
field analysis. It has no mechanical fix: the TTL was already an hour. It is rule 4.

## The levers

Each lever is a switch in `models/levers.py`, read in one place, settable per project in
`harness.yaml` (`dispatch:`) or per run by environment (`MAD_HARNESS_*`), and recorded on
every dispatch. **A default moves only on `ab-report.sh` numbers whose spreads separate,
and each move is its own patch release with the measurement in
[upgrading.md](../upgrading.md).** The one exception is a lever that only removes.

| lever | measured | default | since |
|---|---|---|---|
| `cache_ttl` | null — 5 runs/arm, cost per run $1.38 vs $1.39; the 5-minute TTL caused no expiry misses in a continuously turning worker | unset (the CLI's rule); set `5m` if your test commands finish inside five minutes | 0.10.2 |
| `static_prefix` | null alone — $5.98 vs $6.24 per 8-worker wave; the shareable prefix is already shared, and what a worker writes to cache is ~1k tokens per turn of new context, not the prefix | off | 0.10.2 |
| `stagger_seconds` | ≈$0.09 per 8-worker wave — it fixes only the simultaneous-start race, where 1 in 8 finds nothing cached | 0; harmless to set `8` on wide waves | 0.10.4 |
| `task_budget_tokens` | **−32% cost per run, spreads apart** — a worker told its budget paces; one that is not is cut off from behind by the ceiling it never sees | worker tier 400,000; raise per project | 0.10.4 |
| `preload` (a candidate skill in the writers' system prompt) | **−24% cost per run, output −36%, spreads apart** — writers given `evidence-gathering` make fewer, larger tool calls | the writers declare it, and a declaration is delivered | 0.10.5 |
| `lean_catalog` | request-level, deterministic: a worker's first request 26,130 → 22,743 tokens with 17 bundled CLI skills and 10 orchestrator commands out of its Skill catalog; with cloud connectors off, 21,028 | **on** — the exception: it removes rather than changes | 0.10.8 |
| `plan_tiers` | pending — `ab.sh plan_tiers --plan-only --runs 3`; the arithmetic that motivated it: §3 on a 3-task epic cost 29 min and $7.33 against $1.07 of building, the architect at strategic and the audit at strong ~4 min per dispatch in strict sequence | off | 0.10.28 |
| *(not a lever)* the declared doctrine | measured as a switch first: +61% cost per run ($2.35 → $3.78), spreads apart — and the arm that carried it ran mutation testing in 22% of sessions against 6%, while a headless epic without it failed L2 for decorative assertions. ~$0.16 of the +$0.59 per dispatch is carriage; the rest is the doctrine being *followed*. So it is no longer a switch: every skill an agent declares is in its system prompt on every dispatch, and the rig judges arms with the lenses (`--lenses`) so "cheaper by doing less" reads as a lower first-pass rate | always | 0.10.18 |
| `tiers:` (per-agent override) | a switch for tier-splitting a lens; a gate's catch rate is measured in the field before its tier moves for everyone | off | 0.10.10 |

Measured in [`harness/wavelab/`](../../harness/wavelab/README.md): the same seeded epic,
N runs per arm, fresh repositories, frozen plugin code, every dispatch tagged `lever:arm:run`.
The field confirms a lever's direction; only the same work, repeated, can size it — the
analysis that motivated the series measured 30× token variance on identical tasks.

## Where the money is

Measured on a field campaign: the orchestrator 52%, the planning lenses 34%, the workers
15%. The orchestrator's context averaged ~210k tokens during the campaign and ~380k over
its session, so every tool call it makes re-reads that — three to six times what the same
call costs a worker. What filled it: 35% its own outputs, 35% injected text — built-in
research agents' returns arriving whole (36–45k characters each, as the task-notification
of a background run), a 230k-token reference skill, the loop skill — and 7% tool results.

Four rules follow, stated once in [`harness/orchestrator-card.md`](../../harness/orchestrator-card.md),
carried by every command, and printed by `swarm/pinned.sh` at every session start, resume
and compaction — the session they were measured on never compacted, so a compaction-only
trigger would have fired zero times on it:

1. **Never load reference material into the orchestrator** — a built-in agent loads it,
   answers, and dies with it; and because nothing bounds a built-in agent's return, ask it
   for a few lines or a path. One reference skill loaded in an orchestrator cost $11.21
   re-sent over the 64 turns that followed; ~$2 in a subagent.
2. **One call where five would do** — `preflight.sh`, `apply-plan.sh`, `close-epic.sh`
   are whole sequences; `scan.sh`, `peek.sh`, `run.sh` batch reads and runs.
3. **Artefacts by path** — `dispatch.sh --digest` keeps a plugin agent's result in a file
   and prints its head and path; `tk.sh note --file` attaches it; the result never passes
   through the orchestrator's context.
4. **An hour idle re-writes the whole context** — the next request pays the write rate on
   every token. Back at a large session, weigh what its context is worth against that, or
   start fresh; a campaign gets a fresh session of its own.

A fifth is about the session itself: **an interactive campaign is one epic per session.** An
epic leaves ~300k tokens in the orchestrator's context; the next epic's ~110 requests would
carry that for ~33M tokens — about the whole orchestrator cost of the measured campaign —
and the next epic is loaded from the tracker anyway. The epic boundary is the one moment
where disk equals truth (everything pushed, no claims, no worktrees, no slot), so the loop
ends the invocation there with `/clear`, never `/compact`: a summary is the only thing that
can be wrong. `/campaign-auto` in a terminal cannot end its own session; `swarm/campaign.sh`
runs each epic as a fresh headless `campaign-orchestrator` session through the dispatcher —
measured first: from inside its sandbox it ran pre-flight, tracker writes, a nested worker,
the merge, the gate and the push with zero denials.

And two more that are mechanisms rather than rules: every plugin agent goes through
`dispatch.sh` (a `PreToolUse` hook refuses the Agent tool for them — 67.2M tokens went
through it uncapped and unrecorded in five field sessions), and a revision after a failed
audit is a fresh dispatch carrying the findings by path, never a resumed one (a resumed
lens re-wrote its whole prior conversation at the write rate: $1.95 against $0.16).

## Workers

The single largest worker cost found so far was not a rule but a leak: every worker
inherited the dispatcher's `MAD_HARNESS_CALLER_PWD` through the SDK's environment merge,
so its `run.sh` tested the primary checkout, and it spent the turns finding out why — the
same task ran 77 turns and $1.35 before the scrub, 23 turns and $0.24 after, and the
denials it collected along the way (19 of 21 workers went reading the wrappers) went to
zero. `worker-protocol` and `evidence-gathering` — which every writer and lens declares —
carry the worker-side rules: read in windows (`peek.sh path:START-END`, never a whole file; a
100-line window resolved 5.3 points more than whole-file reads on SWE-bench Lite), send
long output to a file first, and never re-run a suite raw to see output `run.sh` already
kept at `.harness/run/out/<stack>-<key>.log`. `results%` is how you know.

## Ceilings

`max_budget_usd` is a circuit breaker, not a guarantee: the CLI checks it between calls,
so a single large call can overshoot (measured 22×). It stops a runaway loop; it does not
bound one call. A budget kill keeps the transcript the run produced, records the spend
that caused it, and exits 3 from `dispatch.sh` — the swarm routes it rather than reading a
stack trace. `task_budget_tokens` is the budget the model is *told*, which is why it
paces; the two are different levers, and the second is the one that moved the number.
