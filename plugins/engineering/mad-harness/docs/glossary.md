# Glossary

One line each, and a link to the page that owns the term. This is an index, not a second
set of definitions — where an entry and a page disagree, the page is right.

Terms are grouped by what you are doing when you meet them.

## Running work

| term | is |
|---|---|
| **epic** | a unit of work the loop takes end to end: specify, design, decompose, build, verify, fold in. One epic per session — [the loop](concepts/the-loop.md) |
| **task** | one record in the graph: acceptance criteria, dependencies, a lane. What a single worker implements and a lens judges |
| **DAG** | the dependency graph of an epic's tasks. Scheduling is a graph walk, not a model decision — [tasks and tracking](concepts/tasks-and-tracking.md) |
| **wave** | the set of tasks with no unmet dependencies, dispatched in parallel. The next wave starts when their blockers close — [wave lifecycle](reference/swarm.md) |
| **swarm** | running one wave: `/swarm`. Dispatch in parallel, merge serially, gate once |
| **grind** | the serial alternative to a wave: one task at a time, same verification |
| **campaign** | iterating the loop over the epic queue. `/campaign` stops at each epic boundary; `/campaign-auto` does not |
| **lane** | a class of work with its own concurrency cap and technology card — backend, frontend, e2e, docs |
| **claim** | the `O_EXCL` file a worker takes on a task, so a double-dispatch loses rather than corrupting |
| **merge slot** | the single lease that serialises merges. Parallel work, one merge at a time |
| **gate** | the whole-repo suite run **once**, on the merged tree — not per branch, because a defect that appears when two workers' changes meet is invisible per branch |
| **fold-in** | writing an epic's accepted proposal and design into the durable docs, then archiving its staging folder. `close-epic.sh` refuses the close until it is done |
| **staging folder** | the per-epic scratch directory holding an in-flight proposal, design and plan before fold-in — [spec-lifecycle](../skills/spec-lifecycle/SKILL.md) |

## Who does the work

| term | is |
|---|---|
| **agent** | *who* does the work — a markdown definition naming its tools, isolation and doctrine. It no longer names what runs it: the activity does. [Agents](reference/agents.md) |
| **command** | *what* to do now — a slash command an operator types. [Commands](reference/commands.md) |
| **skill** | *how* that kind of work is done well. Loaded per agent, so doctrine an agent never needs costs it nothing. [Skills](reference/skills.md) |
| **doctrine** | every skill an agent declares, assembled into its system prompt on every dispatch. Not a switch: a declared skill that cannot be found refuses the dispatch |
| **orchestrator** | the session driving the loop — it dispatches every other agent and edits no application code itself |
| **worker** | a writer agent implementing one task in its own git worktree |
| **lens** | a verification agent judging completed work. Four of them, each given deliberately different evidence — [verification](concepts/verification.md) |

## Routing and cost

| term | is |
|---|---|
| **activity** | the named unit of dispatched work — `verify.spec`, `design.create`. The thing with a model, a ceiling and a cost record, and the key everything routes on — [agents, activities and strengths](concepts/agents-and-activities.md) |
| **strength** | a named model configuration: provider, model, thinking, and nothing else. `mid`, `strong`, `elite` ship. Replaced `tier` in 0.12.0, which also carried a ceiling and an ordering that were facts about the work |
| **complexity** | `simple` \| `standard` \| `complex`, read from an epic's own surface. An activity may declare a different chain or budget per reading; `standard` is where "cannot tell" lands |
| **chain** | an activity's ordered `strengths`. Its head runs; the rest are where an explicit `ESCALATE` goes. Per activity, because "up from here" depends on the work rather than on the engine |
| **provenance** | whether a resolved strength came from the plugin or the project (`strength_source`). A project patches the same block the plugin ships, so this is not a precedence rank — [dispatch](reference/dispatch.md#strength-resolution) |
| **~~ladder~~** | *removed in 0.12.0.* A single global order over every tier, validated on every config in service of a function that had no caller — and it forced a cross-vendor ranking nobody can justify. See **chain** |
| **dispatch** | one agent invocation through the boundary: an activity, a strength, a ceiling, a sandbox, a permission profile, its doctrine, and one telemetry event — [dispatch](reference/dispatch.md) |
| **ceiling** | `max_budget_usd`, a circuit breaker per dispatch, declared by the activity and optional. Not a hard cap — [cost](concepts/cost.md#ceilings) |
| **told budget** | `task_budget_tokens`, the budget the model is *told* it has, so it paces. The ceiling is the one it never sees |
| **provider** | where a strength's model is served from. Anything reached through the Anthropic-compatible path — [providers](concepts/providers.md) |
| **pocket** | which account a dispatch spends from — `metered` (invoiced) or `subscription` (a plan's allowance). Both real, never summed |
| **lever** | a measured switch affecting cost, recorded on every dispatch, off until an A/B sized it — [cost](concepts/cost.md#the-levers) |
| **arm** | one side of an A/B comparison — the lever off, and the lever on — [measurement](guides/measurement.md) |
| **spreads separate** | the verdict that licenses a change: the two arms' interquartile ranges do not overlap. Anything else is noise |

## Verifying

| term | is |
|---|---|
| **brief** | the evidence pack built once per task and handed to every lens: the task, its criteria, the files changed, and — for the lenses allowed it — the diff |
| **verdict** | a lens's conclusion: `PASS`, `FAIL`, or `NONE`. `NONE` means it could not judge and is never a pass |
| **unanimity** | any FAIL from any lens blocks the task. A boolean, not a judgement |
| **blocking / filed** | a finding that holds the task open, versus one recorded as work for later. Severity decides, so the gate neither stalls waves on taste nor gets ignored |
| **first-pass rate** | the share of tasks that passed every lens without remediation. The quality counterweight to a cost number |
| **decision** | a product, spec or design question the swarm structurally cannot answer. Raised from any stage; `park` gates the epic until it is answered |

## Configuring

| term | is |
|---|---|
| **stack** | a module saying how to *run* things in a toolchain: restore dependencies, isolate a worker, run the suite — [stacks](reference/stacks.md) |
| **framework** | a module saying how to *write* things well in a technology. Independent of the stack, so one toolchain serves many frameworks |
| **area** | a group of changed paths that decides which lens a change must face — how `verifier-security` knows to fire |
| **record** | one task in whichever tracker backend is configured. `beads` rows or markdown files, behind one port contract |
| **backend / port** | the tracker implementation, and the four interfaces it satisfies — [tracker ports](reference/tracker-ports.md) |
| **molecules** | a backend capability flag: whether it can create a parent and its children in one call. Absent, the caller falls back rather than failing |
| **spec index** | the pointer-only map of what the corpus already says about a requirement, so no question is asked whose answer the repo holds |

## Three different things are called a card

This one collides, so it is worth stating plainly:

| | is | reaches |
|---|---|---|
| **orchestrator card** | the loop's own rules, mirrored byte-identical into every command | any session running a command |
| **technology card** | a lane's stack and framework facts, injected into a dispatch | the agent doing that lane's work |
| **complexity card** | a reading of one epic's surface, used to pick which complexity bucket a planning activity resolves at | the routing, before the stage runs |

None of them is the others. If a sentence says "the card", the surrounding section says which.

## See also

[Concepts](concepts/) for why each thing is shaped the way it is; [reference](reference/) to
look up its schema.
