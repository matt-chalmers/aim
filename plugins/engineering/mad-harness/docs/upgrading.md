# Upgrading

How a repository that uses this harness learns the plugin moved, and what it does about it.

## The mechanism

`claude plugin update` replaces the plugin cache wholesale. It ships no hook, and it changes
nothing in your repository — so on its own, a `harness.yaml` written for 0.9.0 runs under
0.9.1 forever, silently missing every block the newer version reads.

The stamp is what closes that gap. `harness.yaml` carries the plugin version it was last
reviewed against:

```yaml
harness:
  version: 0.9.1
```

[`check-project-config.sh`](reference/checks.md) compares it with the installed plugin and
reports one of:

| it says | meaning | what happens |
|---|---|---|
| `harness: 0.9.1  installed plugin 0.9.1` | current | nothing |
| `UPGRADE: … written for 0.9.1; 0.10.0 is installed` | a **minor or major** bump | advisory by hand; `--strict` exits 3, and the `/campaign` and `/swarm` pre-flights run it that way, so a run **stops** until the config is reviewed |
| `WARN: … stamped 0.9.1; 0.9.2 is installed … re-stamp` | a **patch** bump | never blocks; re-stamp when convenient |
| `UPGRADE: … carries no harness.version` | written before stamping existed | as above — treated as older than every note below |
| `WARN: … stamped 0.9.2 but the installed plugin is 0.9.1` | the **plugin** is behind | `claude plugin update mad-harness@aim` |

`/harness-setup` is the upgrade path. On a repository that already has a `harness.yaml` it
reads the notes below, applies every section newer than the stamp — oldest first — and
re-stamps. It never rewrites a block the owner already settled.

**The version rule.** A patch release (`0.9.1 → 0.9.2`) never changes what `harness.yaml`
must say, so it never stops a run. Any change that adds, renames or reinterprets a config
block bumps **minor**. That is a promise the plugin makes, not something the check can
verify — so a release that breaks it is a bug.

**Discipline on the plugin side**, enforced by the test suite: every version has a section
here (even if it says "nothing to do"), and the template and the harness's own config are
stamped with the current version. A bump cannot ship without saying what it asks of you.

## Notes per version

Oldest first, so `/harness-setup` applies them in order. Each item is **mechanical** —
applied without asking, then reported — or **ask the owner** — it needs a fact only the
owner has, asked once with whatever the repository already answers.

### 0.9.0

The first release. A config written for it is the baseline; nothing to apply.

### 0.9.1

- **ask the owner** — declare `ports:`, every TCP port the project's servers bind, by
  name. The pre-flight probes them for a server left running by a killed run. Read the
  candidates off the stack modules and the framework's dev-server convention (Next.js
  3000, Django 8000) and confirm; `ports: {}` if nothing listens. Absent, the config check
  warns on every run.
- **mechanical** — rename a `tasks:` block to `beads:`. The template shipped the wrong name;
  the code and this skill always read `beads.prefix`. Only present in configs copied from
  the 0.9.0 template.
- **mechanical** — nothing to change for it, but say so: every script now finds the
  project from the directory it is called in, so `MAD_HARNESS_REPO=…` prefixes added as a
  workaround are no longer needed. A tracker that cannot find its database now fails
  loudly rather than reporting an empty backlog. *(Correction: 0.9.1 missed `tk.sh
  slot-check`, which still needed the variable. 0.9.2 fixed it — on 0.9.1, keep the
  export until you are past it.)*
- **mechanical** — stamp `harness.version`.

### 0.9.2

A code fix, no config change. `tk.sh slot-check` — the first tracker call in a campaign
pre-flight — resolved the repository on its own, from the process cwd, and failed with
"…/harness is not inside a git repository" when the plugin runs from the cache. It now
resolves the way everything else does. `MAD_HARNESS_REPO` is not required; if you set it
as a workaround, drop it.

- **mechanical** — re-stamp `harness.version`, when convenient.

### 0.9.3

Two field bugs, no config change.

- `worktree-sweep.sh` treated any untracked file it did not recognise by name as work, so
  a worktree holding scratch an earlier harness version wrote (`.swarm-pytest.env`,
  `.swarm/`) read as DIRTY and could never be reclaimed. Everything the harness writes into
  a worktree starts `.swarm`; the sweep now treats that as a namespace. Stranded worktrees
  held only by such files are reclaimed by the next `--apply`.
- `spec-index-status.sh` ran its body under the system `python3`, which has no PyYAML, and
  answered "PyYAML required" for every epic. It runs under the harness venv now, so the
  §3a REUSE / DELTA / REBUILD decision is mechanical again.

- **mechanical** — add `.swarm*` to the project's `.gitignore`, beside the `.harness/`
  lines from setup. Worker scratch then never shows in a worktree's status at all, which
  does not depend on the sweep's pattern staying current.
- **mechanical** — re-stamp `harness.version`, when convenient.

### 0.9.4

One bug, no config change — and the most consequential so far. `tk.sh show <id>` without
`--json` printed the same one-line row `list` does: id, status, type, title. Nothing else.
Eighteen prompt sites — every worker, every verifier, the planner, the architect — read a
task through it and are told to expect "description, acceptance criteria, dependencies,
notes". Every worker built, and every verifier judged, against a title. `show` now prints
the whole record; `list` and `ready` keep the scannable row; `--json` is unchanged.

Work verified under 0.9.0–0.9.3 was verified against titles. Re-verify anything that
matters before trusting its PASS.

- **mechanical** — re-stamp `harness.version`, when convenient.

### 0.9.5

Four field bugs from one `/campaign-auto` run, and a noise fix. No config change.

- **The sweep now sees orphaned refs.** `worktree-sweep.sh` classified worktree
  *directories* only, so a worker branch whose directory was gone — including by the sweep's
  own "remove the worktree, keep the ref" path — was invisible, and every later sweep reported
  clean while the ref held real work. A second pass walks every `harness-w*` and
  `worktree-agent-*` ref with no worktree and classifies it by the task ids in its commits:
  merged (deleted under `--apply`), **IN FLIGHT** (a task still open — kept, reported loudly,
  adopt it before re-dispatching), STALE (all closed — kept unless `--apply --prune-orphans`),
  UNKNOWN (kept). **Run the sweep once now; an IN FLIGHT count above zero is work nobody is
  holding.**
- **`tk.sh update --acceptance`.** The field the verifier judges against was not writable
  through the port; the planner had to reach around to `bd`, which defeats `--readonly` and
  does not exist under mdfiles. It is a first-class `Task` field now, on both backends, and
  `show` renders it under its own rule.
- **Telemetry records an outcome.** `campaign-telemetry.sh record … --outcome parked|stopped`
  files under its own category; the reader prints every row with its outcome and draws the
  trend through closed epics only. Rows recorded as closed for epics that parked before this
  release are mislabelled — discount them when reading the series.
- **`check-blocking-prose.sh`** no longer credits a record for "a separate sibling bead
  blocked on X".
- **No more `VIRTUAL_ENV … does not match` warnings.** Every wrapper scrubs the variable before
  `uv run`; a project's own activated venv no longer produces two lines of noise per call.

- **mechanical** — re-stamp `harness.version`, when convenient.

### 0.9.6

**A resumed run adopts work instead of redoing it.** No config change, but a change to what
the loop does after a stoppage — which had been the biggest source of orphaned work.

Until now, a run stopped after a worker started — the environment killed mid-wave, a lens
still running, a branch committed but not merged — was resumed by dispatching the task
*fresh*: a new branch from HEAD, beside the branch that already held the work. Now:

- `swarm/resume-point.sh <task-id>` answers, per task: **MERGE** (committed and verified at
  this head), **VERIFY** (committed, no verdict), **REATTACH** (a worktree holds uncommitted
  work), **MERGED**, or **FRESH**. `/swarm` step 5 asks it before every writer dispatch.
- `dispatch.sh … --resume <branch>` attaches the worker to the existing branch — reusing its
  live worktree if there is one — and prefixes the prompt with *RESUMING — do not start over*.
- The lens step records `VERIFIED <sha>` on the task when every lens passes, which is what
  makes MERGE derivable. Until a task carries one, committed work resumes at VERIFY.
- `/halt` reports each in-flight task's resume point as the handover.

- **mechanical** — nothing in the config. If you have stranded refs from before 0.9.5's
  sweep, the next `/swarm` will find them per task via `resume-point.sh` and adopt them.
- **mechanical** — re-stamp `harness.version`, when convenient.

### 0.9.7

Measurement first — the two items a field cost analysis said to do before any other
optimisation, because nothing else is measurable until a failed dispatch records what it
spent. No config change.

- **A budget kill is an outcome, not a crash.** It used to raise out of the SDK: the output
  file held only a traceback, no cost event was written, and the orchestrator was left to
  interpret a stack trace with the evidence deleted. Now the dispatch records the spend it
  reached, keeps the steps that arrived before the kill, prints `BUDGET EXHAUSTED` with what
  to do, and exits **3** — distinct from a worker that ran and failed. `make models-cost`
  shows kills per tier.
- **The cache is measured per dispatch.** `cache_hit_pct` and `cache_write_pct` are recorded
  on every dispatch event and printed after each run; `make models-cost` shows them
  token-weighted per agent and tier. These are the numbers that had to be reconstructed from
  transcripts by hand.
- **The `worker` ceiling is $3.00**, from $1.50: measured, $1.50 did not cover reading the
  inputs of a task with a 34KB record and twelve criteria. Revisit from the recorded series.

- **mechanical** — re-stamp `harness.version`, when convenient.

### 0.9.8

The last three open field bugs. No config change.

- **Either id form, everywhere; a miss says what it tried.** Staged folders are named
  `<bare-id>-<slug>`; `spec-index-status.sh` globbed the id as given, so the prefixed id every
  other command takes printed a legitimate-looking `REBUILD` and cost a ~120k-token survey for
  an index already on disk. `render-epic.sh` did the reverse and rendered "not planned yet" for
  the bare id. `spec-index-status`, `check-decision-register`, `archive-epic` and `render-epic`
  all accept both forms now, and a lookup that matches nothing is an error that names the
  patterns it tried — never a plausible verdict.
- **`render-epic.sh --write <relative>` writes into the project.** It wrote into the installed
  plugin cache, exit 0 — the documented invocation at every call site. A relative destination is
  resolved against the repository; one that escapes it is refused. Check
  `~/.claude/plugins/cache/aim/mad-harness/*/harness/docs/` for views written there by earlier
  versions and delete them.
- **A DELTA survey now closes its own loop.** `spec-index-status.sh <epic> --stamp [--cite …]`
  moves the index's `generated_sha` / `generated_at` to HEAD; campaign-loop §3a runs it after a
  DELTA survey lands, so the same DELTA no longer re-fires on every run.

- **mechanical** — for any epic that has taken the DELTA path before this release, run
  `spec-index-status.sh <epic> --stamp` once, if its last survey's findings are current.
- **mechanical** — re-stamp `harness.version`, when convenient.

### 0.10.0

**Cost levers as switches, and the rig that sizes them.** A new optional `dispatch:` block
— minor bump by the version rule, but nothing changes unless you write it.

Every lever the field cost analysis ranked is now a switch, **off by default**, read in one
place (`harness/models/levers.py`), flippable per project in `harness.yaml` or per run by
environment, and recorded on every dispatch event (`levers`, `experiment`) so a series can
always be read:

| lever | `harness.yaml` | env |
|---|---|---|
| prompt-cache TTL | `dispatch.cache_ttl: 5m\|1h` | `MAD_HARNESS_CACHE_TTL` |
| static system-prompt prefix across a wave | `dispatch.static_prefix: true` | `MAD_HARNESS_STATIC_PREFIX` |
| stagger worker 1 ahead of the rest | `dispatch.stagger_seconds: 8` | `MAD_HARNESS_STAGGER_SECONDS` |
| API-side task budget the model paces against | `tiers.yaml` `<tier>.task_budget_tokens` | `MAD_HARNESS_TASK_BUDGET_TOKENS` |
| preload a skill into a dispatch | — (agent frontmatter) | `MAD_HARNESS_PRELOAD` |

`harness/wavelab/ab.sh <lever>` runs the same seeded epic N times per arm in fresh
repositories — up to an eight-worker fan-out — and `ab-report.sh` reads the series back as
medians and interquartile ranges, refusing to call overlapping spreads a finding. Defaults
will move only on those numbers; each move will be its own note here with the measurement.

Also: `wavelab/check-plugin-fresh.sh` looked for the plugin cache under the plugin's own
name rather than the marketplace's and refused every `reset.sh`; fixed.

- **mechanical** — nothing. Leave `dispatch:` unset until a measured default lands; set a
  lever early only if you want to measure it yourself with `make models-cost`.
- **mechanical** — re-stamp `harness.version`, when convenient.

### 0.10.1

Four field bugs from a `/halt release`, and a fix to the A/B rig. No config change.

- **`resume-point.sh` never says "landed" any more.** A killed worker's branch with zero
  commits read as MERGED — "nothing to adopt, close the task" — because main's log happened
  to mention the task id. An empty branch and one that fast-forwarded into main are
  indistinguishable from the ref; nothing ahead of main is FRESH, always. A false FRESH costs
  a redundant dispatch the task's own closed status prevents; a false MERGED closes work
  nobody did.
- **`tk.sh claims`** lists every held claim with holder, host, age and liveness. `/halt` §1
  said `list --status in_progress`, which finds tasks from other sessions and not the claimed
  ones — a campaign claim leaves the status `open`.
- **`tk.sh release` says what it did**, clears the record's assignee (`--keep-assignee` to
  not), and `--force` releases a gone worker's claim. It was silent and left the assignee.
- **`/halt` §3 is worktree-aware**: `preserve-worktrees.sh` first (every worktree's diff,
  untracked files and unmerged commits to `.harness/halted-<date>/`), then `resume-point.sh`
  per task, then release, then remove the worktree of anything not worth keeping — which is
  what makes the next dispatch FRESH instead of re-attaching to a killed run's edits. The
  main-tree `git restore` advice is gone.
- **The A/B rig runs frozen code.** Edits made mid-series tripped the fresh check and aborted
  every remaining lever; had it not, later runs would have run different code from earlier
  ones. `ab.sh` now checks the plugin out at HEAD into the series root and dispatches from
  that copy; each run records the commit, and the report flags an arm that mixes them.

- **mechanical** — re-stamp `harness.version`, when convenient.

### 0.10.2

One measured doctrine fix, and the first two lever measurements. No config change.

- **Workers were being denied on 55% of dispatches — for the doctrine's own test.** In the
  lab, 22 of 40 worker dispatches hit a denial, every one the same shape: the "delete the
  implementation, does it go red?" check hand-rolled as a single compound shell command
  (`cp … && cat > … <<EOF … pytest … cp`), which matches no permission rule. Each one wastes
  a turn and a retry. `test-doctrine` §3 now says how to do the check as three granted tool
  calls, and when to use `mutate.sh` instead.
- **`cache_ttl` measured: null.** 5 runs per arm, 2 workers, wave 1. Cost per run $1.38 vs
  $1.39, cache hit 97% vs 98%, write share 3% vs 2%; every spread overlaps. The 5-minute TTL
  caused no expiry misses in a continuously turning worker; the saving is arithmetic on a
  2–3% write share (~5%) and smaller than run-to-run variance. **Default stays unset.** Set
  `dispatch.cache_ttl: 5m` if your test commands finish inside five minutes; one longer gap
  rewrites the whole prefix and wipes the saving.
- **`static_prefix` measured: null on its own.** 5 runs per arm, 8 workers, wave 1. Cost per
  wave $5.98 vs $6.24; cache written per dispatch 49k vs 43k; spreads overlap. Eight workers
  dispatched in one instant all miss the cache together, so a static prefix alone shares
  nothing — that is the `stagger` lever's job, measured next. And what a worker writes to
  cache is ~1,000 tokens per turn of new context, not the prefix, so the ceiling for prefix
  sharing at this workload is ~6% of a wave, not the 5× the cost analysis computed for
  prefix writes alone. **Default stays off** pending the stagger result.

- **mechanical** — re-stamp `harness.version`, when convenient.

### 0.10.3

**A worker's tests were running in the primary checkout, not its worktree.** No config
change; the most consequential fix since 0.9.4, and a lab worker found it.

`REPO` — the project: config, tracker, primary checkout — was also where every stack command
ran and every read tool looked. A dispatched worker stands in its own worktree, so
`verify/run.sh` tested the *primary*'s code: green with the worker's implementation
deliberately broken (a worker did exactly that, twice, and filed the bug), never its own
`.swarm-env` (looked up under the toolchain's subdirectory, where it never was), and
`peek.sh` showed a worker the primary's copy of a file it had just edited. Now there is
`CHECKOUT` — the project root within the tree the caller stands in — and `run.sh`, `scan.sh`,
`peek.sh`, `brief.sh` and the worker env use it; the tracker and config keep `REPO`. The
dispatcher hands a worker the project (`MAD_HARNESS_REPO`) and deliberately not its own
directory, so each wrapper the worker calls records the worktree it was called from.

**Anything a worker reported as green through `run.sh` before this release was a statement
about the primary checkout, not about its change.** The wave gate itself (run in the primary
after merge) was always correct. Re-verify anything that matters.

Also: the lab's wave now dispatches only the seeded epic's tasks — a worker-filed bug was
dispatched as work and spent a whole ceiling.

- **mechanical** — re-stamp `harness.version`, when convenient.

### 0.10.4

**The first measured default moves: every worker-tier dispatch is now told a token budget.**
Two more lever measurements. No config change required.

- **`task_budget` measured: -32% per run, spreads apart.** 5 runs per arm, 2 workers, the
  full three-task epic, on frozen code. Cost per run $2.67 → $1.80 (IQR $2.43–2.74 vs
  $1.69–2.11 — the first lever whose spreads separate), turns 43 → 36 and cost per dispatch
  $0.72 → $0.59 (both overlap). No seeded task was killed on either arm; the one budget
  kill on the `on` arm was the worker-filed bug the unscoped lab wave dispatched as work,
  fixed in 0.10.3, and is excluded. The mechanism is that the model paces against a budget
  it can see, where `max_budget_usd` cuts it off from behind. **Default now set:** the
  worker tier carries `task_budget_tokens: 400000`, ~5× the median worker's total
  prompt+output. A project whose tasks are larger than the lab's raises it with a new
  `dispatch.task_budget_tokens` in `harness.yaml`; the environment override still beats
  both for a series. Every dispatch record now carries `task_budget_tokens` — the budget
  that applied — beside `levers`, which records only what was flipped.
- **`stagger` measured at the request level: ≈$0.09 per 8-worker wave.** The series was
  stopped after two `on` runs once the transcripts showed the mechanism: with the flag off,
  7 of 8 simultaneously-started workers already read the shared prefix (~15k tokens) from
  cache on their first request; one loses the race and writes it. A stagger saves that one
  write. **Default stays 0**; `dispatch.stagger_seconds: 8` is harmless on wide waves and
  not worth a default.
- **mechanical** — re-stamp `harness.version`, when convenient. Nothing else: the new
  default applies on the next dispatch.

### 0.10.5

**The second measured default: the two writers now preload `evidence-gathering`.** No
config change.

- **`preload` measured: -24% per run, spreads apart.** 5 runs per arm, 2 workers, the full
  three-task epic, frozen code. Cost per run $2.40 → $1.83 (IQR $2.08–2.79 vs $1.44–1.83),
  output tokens 17,969 → 11,449 (-36%, spreads apart), turns 45 → 32 and cost per dispatch
  $0.72 → $0.53 (overlap), no kills on either arm. The `on` arm appended the
  `evidence-gathering` skill — the harness's cost model and batch primitives — to each
  writer's prompt; the mechanism is fewer, larger tool calls and less narration, which is
  what the output-token drop says. Dispatches with a permission denial also fell, 8 → 4 of
  15, consistent with workers reaching for the harness scripts instead of hand-rolled
  compound commands. **Default now set:** `fullstack-engineer` and `quality-engineer`
  carry `evidence-gathering` in their `skills:`, which puts them at ~35,700 preloaded
  characters; the per-agent preload budget rises 32,000 → 36,000 with this measurement as
  the argument, and still catches the campaign-loop-sized mistake it exists for.

This closes the cost-lever series from the field cost analysis. Of the five levers, two
moved a default on separated spreads (`task_budget`, 0.10.4; `preload`, here) and three
stay off as measured null or bounded (`cache_ttl` and `static_prefix`, 0.10.2; `stagger`,
0.10.4). Each was measured alone against its own baseline arm, never stacked; whether the
two defaults compound is a second pass, if anyone wants it. In the field, `make
models-cost` after a wave is the check.

- **mechanical** — re-stamp `harness.version`, when convenient. The preload applies on
  the next dispatch.

### 0.10.6

**Tool-result volume is now measured per dispatch, and the reads that made it big have a
rule.** Cost-analysis action #2 ("offload tool results to files; hand the agent paths"),
built from what the transcripts showed rather than as framed. No config change.

- **What the transcripts said first.** Two field workers (0.9.6) carried 205k characters
  of their own tool results per session — ≈28% of everything the model read, re-read on
  every turn after the fetch. **None of it was test output**, which workers already
  `| tail`; it was the memories index at turn 2 (18k, carried fifty turns), whole files
  read in one call (38k, 54k) and `grep -A 400` on one document, three times for the
  same section. The lab's workers carry ~7% with nothing over 8k, so the lab cannot
  A/B this lever — it is a field measurement, which is why the first change is telemetry.
- **Every dispatch record now carries its result volume**, read back from the session
  transcript: `tool_results`, `tool_result_chars`, `large_results` (8k+),
  `carried_result_tokens` (chars × later turns ÷ 4) and `result_chars_by_tool`. Absent —
  not zero — when the transcript is not found. `make models-cost` shows `results%` and
  `large` per tier; `ab-report.sh` reads the two totals.
- **`run.sh` keeps every command's whole output** at `.harness/run/out/<stack>-<key>.log`
  in the caller's checkout and reports a digest — the failures the runner named, the last
  lines, the path and line count. Before, the output was captured and discarded and a
  worker that saw `[FAIL]` re-ran the suite raw to learn why, paying the whole output in.
- **`evidence-gathering` says to read in windows** and to send long output to a file
  first — with the field numbers, the SWE-bench Lite finding that a 100-line window
  resolved 5.3 points more than whole-file reads, and the CLI fact that a bash result is
  cut at 30,000 characters with no path. Every writer and lens preloads it.
- **`worker-protocol` no longer duplicates two sections of `evidence-gathering`** (the
  conventions block and where the scripts are): since 0.10.5 every writer preloads both,
  so each copy was paid twice per dispatch. The writers' preload bill falls 35,711 →
  34,558 characters with the new section included. The conventions mirror is two
  carriers, and the check now fails an agent whose preloads include neither, instead of
  assuming the set.
- **Action #3 (`clear_tool_uses` context editing) is not reachable from the dispatch
  path.** The Agent SDK's options carry no context-management field and the CLI does not
  use the API's context editing itself; its own mechanisms are auto-compaction and the
  30k-character bash cut (`BASH_MAX_OUTPUT_LENGTH`, head kept, tail dropped). Recorded
  so nobody re-derives it.

- **mechanical** — re-stamp `harness.version`, when convenient. Run `make models-cost`
  after the next wave: `results%` is the number this release exists to show.

### 0.10.7

**The campaign survives its own compaction, and cache breaks are counted.** Two
suggestions from the field, checked against the transcripts before either was built. No
config change; the hook installs with the plugin.

- **Compaction was losing the ids in flight.** One campaign session's summary kept 2 of
  the 9 task ids the previous 400 rows named and dropped two that were mid-dispatch. The
  loop's rules go the same way: `campaign-loop` arrives through the Skill tool as a
  message, which is what compaction summarises. Workers never compact (0 of 33
  transcripts); the orchestrator does, once or twice per long session, and every field
  bug about abandoned worktrees and re-dispatched tasks sits downstream of it.
  **The plugin now ships a `SessionStart` hook** (`compact|resume|startup`) that runs
  `swarm/pinned.sh`: claims, merge slot, harness worktrees and the loop's pinned rules,
  read from the tracker and git — never from memory — printed into the new context
  whenever a campaign is in flight, and silent otherwise. After a compaction it also
  finds the summary in the transcript and names every pinned id it dropped, which is
  the one check the summary cannot make of itself. The rules are one marked block in
  `campaign-loop` ("After a compaction"), the digest of the sections it points at.
- **Cache breaks per dispatch, derived.** The API's `cache_miss_reason` is null on every
  request in every transcript here, and the CLI's own cache-break diagnostic sits behind
  a flag this build cannot set. So the transcript reader classifies each request whose
  cache read fell short of what the previous one had cached, by the gap that explains it:
  `ttl_5m`, `ttl_1h` or `mutation`. Recorded as `cache_breaks`, `cache_break_reasons` and
  `rewritten_tokens`; `make models-cost` shows `breaks` per tier and `ab-report.sh` reads
  it. Across 7 field and lab worker transcripts: zero breaks — consistent with the 98%
  hit rate and the null `cache_ttl` result. Long test runs in the field are where they
  would appear.
- **Requests, not rows.** The 0.10.6 reader counted transcript rows as turns; the CLI
  writes one row per content block, so a thinking + two tool calls message counted three
  times. Both sides of the result-share ratio were affected equally — 28% in the field
  stands, the lab is ~7% — but `carried_result_tokens` per dispatch is now on requests.

- **mechanical** — re-stamp `harness.version`, when convenient. Restart Claude Code once
  after `plugin update` so the hook registers.

### 0.10.8

**A dispatched agent was never given the doctrine it declares.** Found while evaluating a
suggestion (tool-schema deferral and skill progressive disclosure) under adversarial
review; the suggestion itself is mostly not applicable here, but the review turned up
three things that are. No config change; one default moves on a request-level measurement
and says so.

- **Frontmatter `skills:` do not preload under `--agent` dispatch.** The CLI preloads an
  agent's declared skills only when it spawns that agent through the Agent tool, which
  this harness never does — every agent goes through `dispatch.sh`. Verified twice: a
  dispatched writer and a dispatched lens, asked with no tools whether `test-doctrine`,
  `worker-protocol` or `evidence-gathering` were in their context, answered ABSENT for
  all three (the writer first claimed one was present and quoted a heading that exists
  nowhere). Across 456 worker transcripts the only doctrine that ever reached a worker
  was what it loaded on demand: `test-doctrine` in 62, `worker-protocol` in 2,
  `evidence-gathering` in none. **So 0.10.5's shipped change (evidence-gathering in the
  writers' frontmatter) reached no writer**; the −24% it measured came from the `preload`
  lever, which appends the skill to the prompt — the path that works. New lever
  `dispatch.preload_declared` appends every agent's declared skills that way. **Off until
  sized:** it adds ~13k tokens to a writer's first message, and whether the doctrine pays
  for itself at that size is a run-level question the lab is answering now
  (`ab.sh preload_declared`). Verified live: with it on, the same writer answered PRESENT
  for all three and quoted their real headings.
- **`dispatch.lean_catalog`, on by default — the stated exception to "off until sized".**
  The Skill tool lists every skill a session can see, and a dispatched worker's list held
  17 bundled CLI skills (dataviz, claude-api, keybindings-help…) and the plugin's 10
  orchestrator commands it must never run. The catalog is now the plugin's skills plus
  the project's own `.claude/skills/*`. Measured on a worker's first request: 26,130 →
  22,743 tokens (−13%; ~6% of a field first request), paid at cache-read rate on every
  later turn. It is the exception because it removes rather than changes, and its
  effect is below run variance by construction; `ab.sh lean_catalog` exists to size it
  anyway. One behavioural effect, welcome: in 4 of 463 transcripts a worker answered a
  permission denial by invoking the bundled `update-config` skill to grant itself
  `Write`; that skill is no longer in reach. The review caught a real defect before it
  shipped: a plugin-only list hid a consuming project's own skills — unlisted, and an
  invoke rejected with an error that is not a permission denial, so the dispatcher would
  never have seen it. The union is the fix, with a test.
- **No cloud connectors on a dispatch.** The account's claude.ai "Claude Docs" MCP
  connector attached to every worker (~1.2s) and put ~500 tokens of its instructions
  into every first message, for tools the agent's `tools:` ceiling never lets it call.
  `disableClaudeAiConnectors: true` is now in every dispatch's settings. With the lean
  catalog: 26,130 → 21,028 tokens on the first request (−19.5%).
- **The 0.10.7 hook was firing inside workers.** `SessionStart` runs in a dispatched
  agent too (`startup`), and during a wave claims are always held, so the orchestrator's
  pinned rules and every sibling's worktree landed in each worker's first message
  (~1–2.7k tokens). The hook is silent when the payload names an agent.
- **Tool-schema deferral: not applicable, and the numbers say why.** A worker's whole tool
  set is Bash, Read, Edit, Write, Skill — the CLI drops Grep and Glob when Bash is on,
  which is why 61 transcripts hold zero calls to either — about 2.3k tokens of schema,
  under Anthropic's own "don't defer below ~10 tools" line. The CLI's tool search is on by
  default and inert for an agent whose `tools:` frontmatter fixes the pool; forcing
  `ENABLE_TOOL_SEARCH=true` measured 26,130 → 26,130. And a deferral-hint flip is one of
  the CLI's own recorded cache-break causes. Skill progressive disclosure is already how
  the Skill tool works; what we choose to preload is the doctrine, and 0.10.5 measured
  that paying for it is cheaper than not.

- **Where the field's tokens actually go — the finding that outranks the rest.** The
  review tallied TipDonkey's five campaign sessions of 15–17 September: orchestrator
  163.5M prompt tokens, plugin agents spawned through the **Agent tool** 67.2M, agents
  through `dispatch.sh` 6.9M. Eighteen of the ~22 plugin-agent runs (architect, planner,
  analyst, analyst-survey) went through the Agent tool inside the orchestrator, where
  `/design` and `/plan-swarm` say `dispatch.sh` and `campaign-loop` §3a half-assumes the
  Agent tool. That path has no budget ceiling, no sandbox, no telemetry, and none of
  these levers — every measurement in this series is of the 3% path. Whether the
  read-only agents belong on `dispatch.sh` (ceiling, telemetry, levers) or on the Agent
  tool (frontmatter preloads work there; no worktree) is an owner decision, recorded
  here rather than made.
- **A conformance guard for the catalog.** `make conformance` now connects the real CLI
  once (no API call) and asserts it lists exactly the catalog the dispatcher named: the
  CLI stores the list unvalidated and lists the intersection, so a CLI upgrade that
  changed the matching would shrink a worker's catalog silently. The SDK is lock-pinned
  (0.2.152, bundled CLI 2.1.259) but declared `>=`.

- **mechanical** — re-stamp `harness.version`, when convenient. Nothing to set:
  `lean_catalog` applies on the next dispatch; `preload_declared` waits for its number.

### 0.10.9

**Every plugin agent goes through the dispatcher — now as a mechanism, not a sentence.**
No config change; the hook installs with the plugin. Restart once after `plugin update`.

- **The Agent tool is refused for this plugin's agents.** A `PreToolUse` hook
  (`swarm/guard-agent-tool.sh`) denies `Agent(subagent_type: mad-harness:<agent>)` and
  prints the form to use instead: `dispatch.sh <agent> --prompt-file <path>` as a
  background Bash call. Only this plugin's agents are refused; built-in agents and other
  plugins' pass. 0.10.8 measured why: in five field campaign sessions, plugin agents
  through the Agent tool read 67.2M prompt tokens against 6.9M through the dispatcher —
  eighteen of ~22 architect, planner and analyst runs — with no `--max-budget-usd`
  ceiling, no tier, no sandbox, no cost record and none of the levers. `/swarm` step 5
  had said "not the Agent tool" all along; doctrine failed once, so the rule is now a
  hook with a companion test that proves it can fail.
- **`campaign-loop` §3 says how to dispatch.** It said "dispatch the survey / the
  architect / the planner / the audit" without ever naming the command, which is how the
  drift happened; the section now opens with the five `dispatch.sh` forms and the reason.
  The "Agent tool has no per-dispatch override" line, which half-assumed the wrong path,
  is gone: the tier is `model_tier:` per agent file, resolved against `tiers.yaml`.
- **§6 reports what the campaign cost.** With every agent on the recorded path, `make
  models-cost` (`checks/models-cost.sh`, new wrapper) is the campaign's agent spend rather
  than 3% of it: total, kills, and any tier whose fail% or escalations moved.

**What this changes in your own sessions:** with the plugin enabled, `Agent(mad-harness:…)`
is refused everywhere, interactive included; the denial names the `dispatch.sh` form.

- **mechanical** — re-stamp `harness.version`, when convenient. Nothing to set.

### 0.10.10

**The orchestrator's half of the cost work.** Every earlier lever measured the workers,
which the field's second cost analysis put at 15% of a campaign; the orchestrator was 52%
and the planning lenses 34%. Measured on that orchestrator's own transcript: 237
requests, context 55k → 920k tokens, average ~380k, so every tool call it makes re-reads
~$0.11–0.17 of context — six times what the same call costs a worker. What filled it:
35% its own outputs, 35% injected text (subagent results landing in full, and again as
task-notifications; skill loads), 7% tool results. So the levers at the top are **fewer
turns** and **artefacts by path**, and that is what this release is. No config change.

- **`dispatch.sh --digest [N]`.** Every dispatch now writes the agent's whole result to
  `.harness/run/out/dispatch-<agent>-<task>-<time>.md` (`--out` to name it); with
  `--digest` stdout carries the first N lines (default 40) and the absolute path. The
  four largest things in the campaign orchestrator's context were subagent results of
  45k, 43k, 36k and 23k characters, each re-read on every later turn. Without the flag
  the output is unchanged plus one trailing `full: <path>` line.
- **`tk.sh note <id> --file <path>`** and `update --append-notes-file`. §3b had the
  orchestrator copy the architect's design into the epic note — read in, emitted again,
  paid twice at its context size. Now the digest's file is attached by path.
- **Three scripts replace three model-driven sequences.** `preflight.sh` is §0's six
  checks as one call (exit 3 = config needs the upgrade, as before). `apply-plan.sh` is
  §3e+§3f: the planner's command block now carries labels (`T1: … create …`,
  `dep T2 T1`); the script validates the whole plan before writing anything, runs it,
  resolves labels to the ids the tracker returned, records the map under `.harness/run/`
  so a rerun skips what exists, and ends with `validate` and the render — one call for
  what was ten to thirty. `close-epic.sh` is §5's mechanics: blocking-prose, decision
  register and staging/archive checks (nothing written if any fails), then close, export,
  the tracker commit, `pull --rebase` + `push`, `autosync on` — in an order that can no
  longer be got wrong; `--check` runs the checks only, `--no-push` stops after the commit.
  The tracker port gains `export_path` (what the commit stages) — `.beads/issues.jsonl`
  for beads, the configured directory for mdfiles.
- **§3d's revision is a fresh dispatch, never a resume.** Measured: resuming a lens
  rebuilt its whole prior conversation at the cache-write rate — $1.95 for two
  round-trips against $0.16 — because a resumed prompt does not match the cached prefix.
  The planner's revision now gets the audit's output file path in a new prompt. (A 1h TTL
  was the doc's alternative; dispatches already write at 1h, so the miss was the mismatch.)
- **`tiers:` in harness.yaml** — per-agent tier overrides, e.g. `verifier-spec: worker`.
  The A/B switch for tier-splitting the verifiers as `analyst-survey`/`analyst` already
  are; a switch and not a default because a verification gate's catch rate has to be
  measured in the field before its tier moves for everyone. Policy still forces high-risk
  up over it; the dispatch record's `reason` says `project override`, so
  `make models-cost` can split by arm; `check-model-config` judges the plugin's defaults,
  not a project's overrides; the auto-repair mechanism refuses to write it.
- **`session-cost.sh <transcript | session-id>`** — the measurement the cost analysis did
  by hand for $69: one session's context curve in tokens, what grew it, every jump over
  15k and what landed it. Verified against the field transcript: reproduces the analysis
  exactly — and adds what the hand analysis missed: that session sat idle for over an
  hour four times across its three days, and each time the next request re-wrote its
  ~900k-token prefix at the cache-write rate — 4.16M tokens, more than the campaign's
  whole orchestrator spend. A fresh session per campaign (the analysis's own O1b) is
  also the fix for that.
- **The loop tells the orchestrator what it costs** (§0) and to load `evidence-gathering`
  once — its batch primitives are worth six times to the orchestrator what they are to
  the worker they were written for.
- **Not built, and why:** context editing for the orchestrator (A5) has no CLI or SDK
  knob (0.10.7); the pinned-state hook is the "pin the rules" half.

- **mechanical** — re-stamp `harness.version`, when convenient. Nothing to set: `tiers:`
  is off until you write it.

### 0.10.11

**The orchestrator card.** From the field: a project had written the orchestrator's cost
rules into its own `CLAUDE.md` because the plugin stated them in one place (`campaign-loop`
§0) that only `/campaign` reads, as a message, which is what a compaction discards. That is
a plugin gap, and the plugin already had the mechanism for it. No config change; the
project-local copy can go.

- **`harness/orchestrator-card.md`** — ~270 tokens: the three rules (never load reference
  material into yourself — delegate the read to a built-in agent; one call where five
  would do; artefacts by path), each with its measurement, plus the dispatcher rule. It is
  the stack-card pattern one level up: a budgeted block in the prompt, doctrine on demand.
- **Every command carries it**, byte-identical, mirrored by `check-orchestrator-card.sh
  --write` and verified by `make check` — ten copies are only safe when divergence is
  mechanical. A session becomes an orchestrator by running a command, so the command text
  is where the rules are loaded.
- **`pinned.sh` prints it after every compaction and resume, campaign or not.** The
  $11.21 reference load that measured rule 1 happened in a session whose campaign had
  just ended, so gating the card on "in flight" would have missed it; a session that
  compacts has a large context by definition, and that is the condition of the rules.
  The pinned *state* stays the campaign's alone.
- **Not built: a hook denying large reads (the field's proposed third mechanism).** Two
  numbers decide it. The orchestrator's tool results were 7% of its context and its
  largest single read ~3k tokens — the 28% figure was the workers', measured per dispatch
  since 0.10.6 and addressed by the doctrine they now carry. And rule 1's measured event
  was a Skill load, whose size a hook cannot learn (bundled skills sit under a hashed
  temporary path), so a category refusal would deny a 200-token skill at the price of a
  30k-token subagent. Rule 1 is therefore a card rule with its number, as rules 2 and 3
  are; the one mechanically enforceable orchestrator rule — plugin agents go through the
  dispatcher — already is a hook (0.10.9).

- **mechanical** — re-stamp `harness.version`, when convenient. If your `CLAUDE.md`
  restates these rules, trim it to a pointer: the card applies to any long session in a
  plugin-enabled repository, not only a campaign.

### 0.10.12

**The card, corrected by the field.** A consuming project verified 0.10.11 against its
transcripts and sent three points and three corrections; all six were right. No config
change; restart once so the hook picks up its wider trigger.

- **The card prints at session start too.** The session the rules were measured on grew
  55k → 839k over 225 requests and never compacted, so a compaction-or-resume trigger
  fires zero times on that shape. A command session gets the card from the command text;
  the residual is a long session that runs no plugin command, and its first request is the
  cheapest moment on the curve. ~300 tokens, silent inside dispatched agents.
- **Rule 1 asks for a bounded return.** Nothing caps what a built-in agent sends back, and
  the biggest things in the measured orchestrator's context were exactly those: research
  agents' returns of 36–45k characters. `--digest` bounds plugin agents only. The card now
  says: ask for a few lines, or a path.
- **Rule 4 is new: an hour idle re-writes the whole context.** The break measurement in
  0.10.10 had no rule attached. In the measured session four idle gaps of over an hour each
  made the next request re-write its 700–920k-token context at the write rate — 3.5M
  tokens, more than the campaign's orchestrator spend and the largest single cost in either
  field analysis. The TTL was already an hour, so the lever is behavioural: back at a large
  session, weigh its context against that or start fresh. The card fires on `resume`, which
  is the moment.
- **Corrections to earlier notes.** (1) 0.10.10 said subagent results "landed in full, and
  again as task-notifications": they arrive once — a background agent's as the
  notification, a foreground agent's as the tool result — the claim was wrong, the size was
  not. (2) 0.10.10's "four idle gaps re-wrote 4.16M tokens" was five breaks: four gaps
  (3.46M) and one 700k mutation. (3) 0.10.11 said a hook could not size a bundled skill
  load; it can — bundled skills sit at a globbable path. A read-size hook is still not
  built, on 0.10.9's own rule: doctrine becomes a mechanism when it is measured to have
  failed, and the card is the doctrine; `session-cost.sh` is what would show it failing.
  (4) The 225-vs-237-request discrepancy between the two analyses was neither side's
  dedup: the session kept being used between the two readings (241 now).

- **mechanical** — re-stamp `harness.version`, when convenient. A `CLAUDE.md` that
  restates the rules trims to a pointer; the one sentence worth keeping there is the
  standing authorisation to delegate reads to a built-in agent, since some sessions carry a
  rule that only a user, `CLAUDE.md` or a skill may grant it.

### 0.10.13

**An interactive campaign is one epic per session.** From a field analysis of what the
orchestrator carries between epics, checked against the loop and the transcripts. No
config change.

- **`/campaign` ends its invocation at the epic boundary.** Nothing in the loop compacted
  or cleared between epics, nothing the model can call does so, and the CLI's own
  compaction fires only near the window's end — a measured session reached 920k without
  it. An epic leaves ~300k tokens in context; the next epic's ~110 requests would carry
  that for ~33M tokens, about the whole orchestrator cost of the measured campaign, and
  the next epic is loaded from the tracker anyway. §6 now runs `pinned.sh --always` to
  confirm nothing is in flight, then ends with the instruction: `/clear`, then `/campaign`.
  `/clear` and never `/compact`: at the boundary disk equals truth, and a summary is the
  only thing that can be wrong. Mid-epic compaction stays what 0.10.7 made it — the
  fallback the pinned-state hook recovers from, never the plan.
- **`/campaign-auto` is unchanged**, and pays the carrying cost, because it cannot end its
  own session. The structural fix — an outer script starting one fresh headless session
  per epic, the lab's own `dispatch-wave.sh` shape one level up — is recorded as the next
  lab project rather than shipped: it makes the orchestrator a dispatched agent that
  dispatches sandboxed workers from inside its own sandbox and pushes from a boundary the
  worker sandbox keeps off the network, none of which has been run. Building it would also
  give the lab an orchestrator to measure, which every orchestrator lever so far has lacked.

- **mechanical** — re-stamp `harness.version`, when convenient. An interactive campaign
  now needs `/clear` + `/campaign` between epics; the loop says when.

### 0.10.14

**`/campaign-auto` as one fresh headless session per epic — and the denial engine it
uncovered.** Researched by running it: five probes of a headless orchestrator inside the
harness boundary, then a whole epic. No config change; one new `network:` field on stack
modules, already set on the shipped ones.

- **A leak, not a doctrine failure, was behind most worker denials.** The SDK builds a
  child's environment as `{**os.environ, **options.env}`, so a variable merely absent from
  `build_env`'s result is inherited from the dispatcher's own process. Every worker
  therefore got the dispatcher's `MAD_HARNESS_CALLER_PWD` — `dispatch.sh`'s wrapper exports
  it — and its `run.sh` resolved `CHECKOUT` to the primary checkout: 0.10.3's fix worked in
  its tests and in no real dispatch. It also got the operator's `VIRTUAL_ENV`, so its every
  `uv run` warned. Measured on 76 denials across 43 lab tasks: ~50 were a worker prefixing
  a harness wrapper with those variables or reproducing the wrapper's body, and 19 of the
  21 workers that did had first seen the warning and read the wrapper's source to learn why
  its test log was in the wrong tree. `_run_sdk` now scrubs both from the process before
  the SDK spawns. **Same task, before and after: 77 turns, $1.35, 2 denials → 23 turns,
  $0.24, none.** One pair; the next series sizes it.
- **The remaining innocuous shape is allowed by a hook.** `VAR=x …/tk.sh …` and
  `env -u X …/run.sh …` are the wrapper, and are allowed as the wrapper is — one simple
  call, an executable under the harness root, never a pipe, a substitution or `git push`,
  in dispatched sessions only. Plain python3 rather than the uv wrapper, because it runs on
  every Bash call. `mutate.sh` keeps its trees under `.harness/run/` in the checkout instead
  of `/tmp` (a sandboxed worker could not read its own mutations file back) and the
  doctrine no longer asks for an env prefix. The lab's seeded config no longer teaches
  `env -u VIRTUAL_ENV`.
- **An `orchestrator` role.** `campaign-orchestrator` (`role: orchestrator` in frontmatter)
  runs one epic of the loop headless, through the dispatcher. The role changes the boundary
  in three ways, each measured: the `git push` deny does not apply and `git`/`make` are
  granted; the sandbox's egress opens to the remote and to each stack's `network:` (its
  package index — nested, a worktree's init runs inside that sandbox, and a nested worker
  died at `uv sync` with `files.pythonhosted.org` denied); and `dispatch.sh` is excluded
  from the sandbox, because a worker spawned inside it could not log in — the CLI's OAuth
  credential is in the keychain, which Seatbelt does not reach. Every sandbox field now
  goes in the SDK's `sandbox` option: the transport replaces the settings' sandbox block
  wholesale, which is why the toolchain-cache write policy in `settings` had never reached
  the CLI either.
- **`swarm/campaign.sh [--max-epics N] [--epic ID]`** is the outer loop: pre-flight once,
  then per open epic a fresh `campaign-orchestrator` dispatch with `--digest` and `--out`,
  stopping on a refused dispatch or a closed usage window (exit 5, as the A/B rig). The
  agent carries the two rules a terminal never needed: a headless session ends the moment
  the model stops calling tools (one probe's orchestrator "waited for a notification" and
  simply ended, killing its worker), so it waits for background dispatches in the foreground
  with `until` loops; and every dispatch names its result file up front.
- **Probes, in order.** (1) Nested dispatch inside the orchestrator's sandbox, a merge, the
  gate, tracker writes: allowed; push and all egress denied — the network key in
  `settings` never reached the CLI. (2) Network fixed: `github.com` and `pypi.org`
  reachable, `example.com` blocked, push ok; the nested worker: `Not logged in`. (3)
  Dispatcher excluded; the orchestrator yielded mid-wave. (4) Foreground wait: the worker
  ran 77 turns, committed, merged, gated, pushed — with the leak above still live. (5) The
  scrub: 23 turns, no denials, the log in the worker's own worktree.
- **Then one whole epic through `campaign.sh`.** A fresh lab, three seeded tasks. The
  orchestrator ran §1–§2 for the epic, the survey (INFERABLE, $0.18), the architect
  ($0.69), the planner ($0.62), the audit (PASS, $1.07), `apply-plan --dry-run` then the
  apply with render, `resume-point` per task, two workers in the background with `--out`
  (25 and 33 turns, $0.29 and $0.39, **zero denials each**), waited in the foreground,
  built both briefs, dispatched six lenses, merged the task all three passed, held the one
  L2 failed (a real finding: unpinned assertions — branch kept, claim released, fixes named
  on the epic), synced the tracker and pushed. Every artefact moved by path. Then it
  stopped itself: 74 turns and $3.19 against the strong tier's $4.00 ceiling, judged not
  enough for a remediation round, the epic left open and honest. Three things came out
  of it, all fixed here:
  - **Every lens was denied `Read` on its own brief.** Briefs followed `TMPDIR`, and the
    lens was handed that directory — but `brief.sh` ran inside the orchestrator's sandbox
    (which sets its own `TMPDIR`) while the excluded `dispatch.sh` computed the grant
    outside it. Six denials, six operator questions filed, and one of those then blocked
    the task's close, which the orchestrator escaped with `bd close --force`. Briefs now
    live at `.harness/run/briefs/` **inside the project** — a lens's own working directory,
    no grant involved.
  - **The orchestrator's ceiling is per epic.** `tiers.yaml` gains `orchestrator:
    max_budget_usd: 25.00` for the role; a field orchestrator spent $16 on one epic.
  - **`campaign.sh` restores `autosync on` however a session ends** — a session that stops
    before §5 restores nothing, and this one left `export.auto: false` behind.

- **mechanical** — re-stamp `harness.version`, when convenient. Restart once so the new
  hook registers. If you run unattended campaigns, `swarm/campaign.sh` is the form; if a
  stack module of your own fetches from a registry, declare it under `network:`.

### 0.10.15

**The consumer's verification is four checks and the worktree probe; the suite is the
author's.** From the field: `harness-setup` §7 listed `make harness-test` beside the four
consumer checks, a target that exists only in the plugin's own Makefile — and a consumer
who ran the suite from the installed cache got fifteen failures. All one cause: the
suite's corpus sweeps enumerate shipped files with `git ls-files` (exactly what ships,
never scratch, refusing to pass on zero files) and its revision readers exercise `peek.sh`
and `brief.py` against HEAD, and a plugin cache is not a git repository. None of those
fifteen said anything about the consumer's configuration. No config change.

- §7 drops `make harness-test` and states the split: `make project`, `make skills`,
  `make models`, `make commands` and the worktree probe are a consumer's verification — the
  whole of it; the suite asserts the harness's internal invariants and belongs to the
  plugin's repository.
- The suite refuses to run outside a git checkout, with one line saying why and what a
  consumer runs instead, rather than fifteen confusing failures. The field's alternative —
  skipping the git-dependent tests and giving the sweeps an `os.walk` fallback — was
  declined: the sweeps would lose their "only tracked files" guarantee, to serve a run
  nobody should be making.

- **mechanical** — re-stamp `harness.version`, when convenient. If your setup notes say to
  run the harness's suite, stop.

### 0.10.16

**`preload_declared` measured: worse. It stays off, and the reasoning in 0.10.8 is
corrected.** 5 runs per arm, 2 workers, the full epic: cost per run $2.35 → $3.78 (+61%,
spreads apart), cost per dispatch $0.59 → $1.18, turns 42 → 60 (overlap). The `on` arm
straddled two plugin commits (the series was stopped by a usage window and resumed after
a release) and every run still carried the environment leak 0.10.14 fixed, so the size is
provisional — the direction is not. Appending a writer's whole declared set (~35k
characters, ~9k tokens) to every prompt costs more than it saves; 0.10.5's −24% came from
`evidence-gathering` alone (~7k characters), which the writers now reach on demand. So:
the CLI does not preload frontmatter `skills:` under `--agent` dispatch, a dispatched
agent loads its doctrine when its body says to, and the numbers say that is the cheaper
path. 0.10.8 called the on-demand path a gap; it is the default because it measured best.

- **mechanical** — re-stamp `harness.version`, when convenient. Nothing to set.

### 0.10.17

**0.10.16 was wrong to call the on-demand path "best": cheaper is not the same as
better, and the rig could not tell them apart.** No config change; `preload_declared` is
now on by default, and the rig measures fidelity.

- **What the +61% was.** The arm that carried the doctrine ran mutation testing in 22% of
  sessions against 6% on demand — where only 26% of workers ever loaded `test-doctrine`
  and none loaded `worker-protocol`. Per dispatch, carrying ~9k tokens of doctrine over 60
  turns at the cache-read rate is ≈$0.16 of the +$0.59; the rest is 18 more turns of
  work the doctrine demands. And the headless epic that ran without it failed L2 for
  decorative assertions and had a worker close its own task before the gate — the two
  rules the two skills exist for. "Cheaper" meant "did less".
- **Default flipped: `preload_declared` on.** Behaviour first. The doctrine an agent
  declares reaches it, at the measured cost, until a measurement that sees quality says
  otherwise. Projects may set it `false` to measure without.
- **The rig now sees quality.** `ab.sh --lenses` runs the three lenses over every landed
  task after each run (`lens-wave.sh` gains L2, `verifier-tests`, the lens that judges
  exactly what the doctrine changes) and keeps the verdicts beside the run's events;
  `ab-report.sh` reports first-pass PASS rate per lens per arm, and splits a judged run's
  cost into writers and lenses so it still compares with the earlier series. The rule for
  moving a default now has the second half it always needed: cost with spreads apart,
  **and** first-pass rate not worse.

- **mechanical** — re-stamp `harness.version`, when convenient. Nothing to set.

### 0.10.18

**Doctrine is not optional. The skills an agent declares are part of its system prompt on
every dispatch — not a lever, not a message.** No config change; `dispatch.preload_declared`
is gone (a config that still sets it fails the check, so nothing is silently ignored).

- **Why it was a switch, and why that was wrong.** The prompt-append existed first as the
  A/B rig's instrument (0.10.0), built on the belief that the frontmatter loaded skills
  into the system prompt in production. When 0.10.8 found the CLI does no such thing on
  the `--agent` path, the instrument was wrapped in a switch under the "off until sized"
  rule — a rule for optimisations, misapplied to a correctness fact the agents themselves
  declare. 0.10.16 then read the switch's cost as a verdict and 0.10.17 corrected it; this
  release removes the switch.
- **Where the doctrine lives now.** `resolve()` assembles every declared skill, in full,
  into `Resolved.doctrine`, and `sdk_options` delivers it as the system prompt's `append`
  (verified live on the `--agent` path). The system prompt is re-sent unchanged on every
  request, so the cache serves it after the first write; a compaction never touches it;
  and nothing an agent does can skip it. A declared skill that cannot be found refuses the
  dispatch, as a missing credential does. `doctrine_chars` is recorded on every dispatch
  event. `check-skills.sh`'s per-agent bill is now literally the size of that append.
- **The rig's `preload` arm** still exists — for measuring a *candidate* skill before an
  agent declares it — and now delivers it the same way, so the arm measures exactly what
  declaring would do. The judged series (`--lenses`) is the measure that matters.

- **mechanical** — remove `dispatch.preload_declared` from `harness.yaml` if you set it;
  re-stamp `harness.version`, when convenient.

### 0.10.19

**Deterministic steps out of the prose, part 1: `tk.sh park` / `tk.sh unpark`, and the
drift an audit found.** No config change.

- **What was wrong.** Parking an epic was two commands — `gate create <epic>` and
  `update <epic> --status blocked` — because beads refuses the blocking edge on an epic while
  still writing the gate issue, so the gate alone never removed the epic from
  `list --type epic --status open`. The loop's text said "both steps, always" in four places
  and the pinned block repeated it after every compaction; `commands/campaign-auto.md` still
  shipped with only the first step, and `campaign.sh`'s queue filters on `status=open`, so a
  headless park would have re-dispatched the epic. A rule stated four times and drifted once
  is a verb the tracker should own. `park` is the gate, a `PARKED <gate>:` note on the epic
  (beads cannot record which epic a gate blocks, so the note is how `unpark` finds it), then
  the status; `unpark` is the mirror and refuses when it cannot tell which gate holds the
  epic. Conformance-tested on both backends' real binaries.
- **The rest of the audit, one line each.** `/swarm` composed a wave with
  `tk.sh ready --label <lane>` while no such flag existed (inline-backticked, so the
  invocation-parses test never saw it) — it exists now. `/swarm` step 9 and `/grind` §10 said
  `git pull --rebase`, which `close_epic.py` had already recorded refuses to start on a beads
  repo after `autosync off` — `--autostash`. `quality-engineer` released the merge slot with
  `slot-acquire release`, not a verb, so the slot stayed held for an hour; and read its diff
  with `git show <sha>`, the ~96k-token path its own preloaded skill bans. `/landit` and
  `/requirements` closed tasks and committed without `tk.sh export`. Three stale
  cross-references in `campaign-loop` (3b→3c, 3c→3e, "move to that epic" against
  one-epic-per-session). The wavelab README called the unanimity rule a judgement.
- **Why an audit, and what follows.** The harness's rule is that a machine-checkable step
  is code with a test and a judgement is a prompt with a lens. 0.10.10–0.10.18 applied it to
  §0, §3e/f, §5 and the outer loop; a sweep of every command, agent and skill found the
  rest — the wave loop, the lens gate, `/halt`, the §3 pipeline, and a per-agent layer of
  copy-pasted procedures. Every drift above was found by that sweep and each is what `make
  check` would have caught as code. The releases that follow move those sequences into
  `harness/`, one cluster at a time, each measured.

- **mechanical** — re-stamp `harness.version`, when convenient.

### 0.10.20

**Deterministic steps out of the prose, part 2: one sync tail, `close-wave.sh`, and the
pre-flight that finishes.** No config change.

- **What was wrong.** `/swarm` step 9, `/grind` §10 and `/halt` §4 each spelled out the same
  ten shell lines for the orchestrator to run one per tool call — close, export, render the
  view, add, commit, pull, push, status, autosync — with the same "in this order, every
  time" warnings, because each was a place a line could be skipped; the recorded incidents
  (a backlog published with a closed record still `in_progress`; `export.auto` left off for
  a session) are those skips. `close_epic.py` (e)-(h) was a fourth copy, in code. It is now
  `models/tracker_sync.py`, and `close-wave.sh` (the wave / the task / a halt's tail) and
  `close-epic.sh` both call it. `close-wave.sh` also settles a contradiction the two
  documents had carried for months: `/swarm` restored `export.auto` every wave, the loop
  said only §5 does — both right for their caller. `--restore-autosync` is passed by a
  run that is the whole run and omitted by a wave inside a campaign. And a rebase that
  pulls in another actor's commits now **stops before the push** with the rest listed by
  hand, so the wave gate is re-run on the rebased tree first — the rule the prose stated
  and nothing enforced.
- **`close-epic.sh` starts earlier.** §5's text still had four lines before the one call
  — every child closed or gated, render the view one last time BEFORE retiring, archive
  the folder, fold-in ② first — with the order the orchestrator's to remember. They are
  the first gates and pre-writes of the call now; the archived folder's `git mv` and stamps
  commit **with** the export (before, the stamps `archive_epic` wrote after the `mv` were
  left dirty by every close); the epic's lease is released after the push.
- **`preflight.sh` finishes the job.** `/swarm` step 1 and campaign-loop §0 still ran
  `git worktree prune`, the sweep, `--apply` and `check-stack-commands.sh --repair` as four
  calls after it, the sweep's IN FLIGHT count read from its text. They are steps now: the
  sweep is a gate — IN FLIGHT > 0 stops the run until each ref is adopted, and a sweep
  output without its summary block is "nothing measured", never zero — and a repair that
  changed `harness.yaml` says so on its line.
- **The orchestrator card's rule 2** names the category ("every `swarm/*.sh` is a whole
  sequence") rather than a list that cannot grow inside the card's 1,200-char budget.

- **mechanical** — re-stamp `harness.version`, when convenient.

### 0.10.21

**Deterministic steps out of the prose, part 3: the lens gate.** No config change.

- **What was wrong.** `/swarm` step 7 and `/grind` §9 had the orchestrator, per task per
  round, build the brief, compute the L4 trigger by hand (with `brief.py` already holding
  every input), write three or four prompt files enforcing by hand that L3's named no
  diff path, dispatch them, read the verdicts by eye, apply unanimity, route, and type the
  `VERIFIED <sha>` line `resume.py` machine-reads — fifteen to twenty calls at the
  orchestrator's context price, all routing. Two defects rode along: the lenses ran in
  the primary at `main` *before* step 8 merged the branch, so L2 re-ran a suite without
  the change and L3 read a repository without it; and "L3 must never see the diff" was a
  request — the diff sat under the brief root every reader was granted, and `brief.md`
  printed its paths. `lens-gate.sh` is the gate as one call: the brief and the trigger,
  the suite once in the branch's worktree, all lenses at once, one verdict parser, the
  note only on unanimity. A lens that hung, was denied, was cut off or returned no
  `VERDICT:` line is NONE, never PASS, and the gate writes nothing.
- **L3's exclusion is physical now.** The diff artefacts live in a sibling root
  (`.harness/run/briefs-diff/`), `brief.md` carries no pointer to them, `verifier-spec`
  declares `evidence: no-diff` and is denied that root on the dispatch
  (`Read(//…/briefs-diff/**)` — deny beats allow, and it covers `cat`/`head`/`sed`), and
  the gate asserts L3's prompt names no such path. L1, L2 and L4 are handed an
  `artefacts.md` by path. `brief.md` also gained the acceptance criteria (every lens re-ran
  `tk.sh show` for them), the commit-hygiene facts, and the L4 trigger section.
- **One verdict parser.** `models/verdict.py` reads `VERDICT:` lines and the worker return
  line. `escalate.classify` used to take the first `·`-token of a worker's return as its
  status while the contract puts the id first, so a BLOCKED return was never recognised;
  it reads through the one parser now.
- **`fanout.sh`** runs N commands at once, each with a timeout, and answers for every one;
  `--detach`/`--wait` replace the headless orchestrator's hand-written
  `until [ -s <path> ]; do sleep 20; done`. **The wave manifest**
  (`.harness/run/waves/<epic>-w<n>.json`) is born here: the gate records each round on it,
  so the circuit breakers and the health signals (0.10.22) are arithmetic over a record
  rather than counters in a context a compaction loses.
- The wavelab's `lens-wave.sh` is a thin caller of the production gate now, so the lab
  exercises the production path and the two cannot drift.

- **mechanical** — re-stamp `harness.version`, when convenient. Briefs built before
  this version have their diff under `<brief>/diff/`; a lens gate rebuilds the brief, so
  nothing needs moving.

### 0.10.22

**Deterministic steps out of the prose, part 4: the wave, on a record.** No config change.

- **What was wrong.** `/swarm` steps 2–3 (compose the wave), 8 (merge and gate) and 10 (the
  four health signals), and `campaign-loop` §4.5 (the circuit breakers), were sequences the
  orchestrator ran by hand at its context price: `tk.sh ready`, clamp to the caps, a
  `resume-point.sh` per candidate, `wc -l` every path against `signals.megafile_lines`,
  pairwise path intersection, `git merge-tree`; then `slot-acquire`, a merge per branch, a
  `run.sh` per stack, `git log` to attribute a red gate, `slot-release`; then a shell
  pipeline in a table cell for the escape rate and a bash `for` loop pasted into the prose
  for the accretion check, with `${MEGAFILE:-1000}` where the declared threshold should
  have been read. And every circuit breaker was a counter across waves that existed only
  in the orchestrator's context — which the skill itself says a compaction loses.
- **Four scripts, one record.** `wave-plan.sh` composes the wave and opens the epic's
  **wave manifest** (`.harness/run/waves/<epic>-w<n>.json`); `fanout.sh`, `lens-gate.sh`,
  `merge-wave.sh` and `close-wave.sh` each write their keys to it under a lock;
  `wave-report.sh` computes the signals from it and `breakers.sh` evaluates the seven
  breakers over it, each trip printed with the table's prescribed action. The judgement
  that stays with the orchestrator is explicit and printed with its inputs: the
  shared-vocabulary and new-file checks, the re-queue-or-resolve call on a conflict, the
  revert on a red gate, and the ACTION on a tripped breaker. The pinned state a compaction
  restores now names the open waves.
- **Rules that became code.** Every branch is verified as a ref and the tree as clean
  BEFORE the merge slot is taken; the slot is released in a `finally`; a conflict is
  aborted and left unmerged, never resolved; a stack with nothing declared is not green;
  git unable to run `merge-tree` is said, never read as clean; a null escape-rate baseline
  is "first measurement", never zero; a manifest with no `dispatched` falls back to
  `planned` and says so.
- The wavelab's `dispatch-wave.sh` and `merge-wave.sh` are thin callers of the production
  scripts now (wave-plan → fanout → lens-gate → merge-wave → close-wave), so the lab
  exercises the production path.

- **mechanical** — re-stamp `harness.version`, when convenient.

### 0.10.23

**Deterministic steps out of the prose, part 5: `/halt`.** No config change.

- **What was wrong.** `/halt` was twenty to twenty-five tool calls at the orchestrator's
  context price, run when something had already gone wrong: seven reads including a shell
  `while` loop over the worktrees, the two-step park, a `resume-point.sh` per claim, then
  "first preserve, then look, then release, then remove — in that order, because each step
  is what makes the next one safe", then the slot, autosync, prune, export, commit, push.
  The recorded incident is an operator following `tk.sh list --status in_progress`
  literally and cleaning up four foreign tasks while leaving the two real ones claimed.
- **`halt.sh assess | pause <epic> | release [<task>…]`.** `assess` reads and prints
  everything a halt looks at (the claims with liveness, from `tk.sh claims` — the
  authority) and changes nothing. `pause` keeps the claims and parks the epic. `release`
  preserves every worktree's work to files FIRST — a failure there stops everything — then
  per task the resume point, `tk.sh release --force`, a note naming where the work was
  preserved and what state it was in, and the worktree removed only for REATTACH, only under
  `--drop-uncommitted`, and only when the preserve step reported that branch. Both writes
  end with the slot released only when its holder is provably gone (an alive holder is a
  FAIL line, never forced), `git worktree prune`, and the shared sync tail with `autosync
  on`. The one decision that stays with the operator — is uncommitted REATTACH work worth
  keeping — is the flag, taken after reading `assess`.

- **mechanical** — re-stamp `harness.version`, when convenient.

### 0.10.24

**Deterministic steps out of the prose, part 6: the worker's prompt, its claim and its
commit.** No config change.

- **`dispatch.sh --task-prompt`.** `/swarm` step 5 had the orchestrator fill a ten-item
  prompt template N times per wave — the task's `tk.sh show` text, the run/commit rules,
  `.swarm-env` "as generated, never retyped", the protocol, the slot id, the ban list, the
  return contract, the task's SPEC INDEX slice, the memory keys, a fidelity defect list.
  Seven of the ten are derivable and the one most often dropped was the slice ("the lens
  failures that cost this campaign most were tasks whose worker never knew which ADR or
  owner decision bound them"). `worker_prompt.build` assembles the record verbatim, how to
  run and commit here, the SPEC INDEX slice the task's `SURFACE:`/`AUTHORITATIVE SPEC`
  lines point at (pointers only; no index is *said*), the matching field-guide keys, and a
  fidelity task's `DEFECTS:` note. The protocol, the ban list and the return contract are
  not repeated — they are `worker-protocol` doctrine in the system prompt since 0.10.18.
  `--prompt-extra <file>` is what the orchestrator adds.
- **The claim precedes the spawn.** A worker's first act was `tk.sh claim`, and one that
  found the task held by a sibling returned `SKIPPED` — after the dispatch had paid its
  whole fixed base (~18.7k tokens) to learn it. The dispatcher claims first, under the
  actor the worker's `.swarm-env` exports (`swarm-w<n>`), so the worker's own claim is
  re-entrant and a lost claim costs nothing. `--no-claim` for the cases that want the old
  behaviour.
- **`commit.sh`.** Both writers carried the same five-step commit sequence in prose
  (`slot-acquire` → `status` → `add` → `commit` → `slot-release`); `quality-engineer`
  spelled the release as `slot-acquire release`, not a verb, so its slot stayed held until
  the stale timeout; a commit that failed mid-sequence left it held too. The script checks
  the index for paths the worker did not name BEFORE taking the slot, refuses the
  tracker's export, stages exactly the named paths, and releases the slot in a `finally`.
- **Removed:** the "bootstrap your worktree" section in both writers, which told them to
  run `swarm-worktree-init.sh` while their preloaded `worker-protocol` said the dispatcher
  already had and they must not. The wavelab's `dispatch-wave.sh` drops its heredoc prompt
  template for `--task-prompt`. `dispatch.main`'s parser is `build_parser()`, and the test
  that parses every documented `dispatch.sh` line uses it rather than a copy of its flags.

- **mechanical** — re-stamp `harness.version`, when convenient.

### 0.10.25

**Deterministic steps out of the prose, part 7: the campaign's queue, lease, signals and
heartbeats.** No config change.

- **`epic-queue.sh`** is §1 and §2 as one call: every open epic P0→P3 then oldest, each
  excluded with its reason — a gate holds it, a `PARKED` note, a lease another machine
  holds — or triaged UNPLANNED / PARTIAL / READY with its dispatchable-on-entry count.
  `campaign.sh` used to do one of §1's five steps and *assume* `status=blocked` covered the
  gate exclusion, which 0.10.19 found it did not; and nothing in code acquired or released
  a lease — cross-machine double work was guarded by prose alone. `campaign.sh` now reads
  the queue from the script, takes each epic's lease before its session and releases it in
  a `finally`, and records the signals with the outcome the session reported.
- **`campaign-signals.sh`** is §6 as a script: the eight signals from the wave manifests,
  the dispatch telemetry, the epic's `ADEQUACY:`/`AUDIT:` notes and git; `--record` files
  them with the outcome, so forgetting `--outcome parked` — which filed a parked epic as a
  catastrophically bad closed one — is no longer a thing the orchestrator can do.
  `campaign.py` gains bands for `l4_dispatch_rate` and `analyst_gate_rate`, which were
  recorded blind.
- **The phase heartbeats** (DISPATCH, COLLECTED, LENSES, GATE, PUSHED) are written by the
  wave scripts that run each phase, not by the orchestrator — they were the most skippable
  lines in the loop.
- **`tk.sh decisions --rank`** walks the DAG once and ranks open decisions by the work each
  unblocks (transitive dependents; epics parked on it). `/decision`'s stated value — "the
  outstanding owner decision that unblocks the most work" — was up to fifty `show` +
  `ready --parent` calls to compute by hand, and in practice three were sampled.

- **mechanical** — re-stamp `harness.version`, when convenient.

### 0.10.26

**Deterministic steps out of the prose, part 8: the §3 sequencer.** No config change.

- **What was wrong.** campaign-loop §3 — survey, fold-in ①, architect, planner, audit,
  apply — was 500 lines and twenty to thirty tool calls per epic at the orchestrator's
  context price, every hop the same: write a prompt naming the previous artefact's path,
  dispatch, read a verdict line, branch. Two copies of the ADEQUATE/INFERABLE/ABSENT table
  had drifted; the audit-retry counter lived in the orchestrator's head; the `ADEQUACY:`
  and `AUDIT:` notes signal ①d exists to count were the lines most often skipped; the
  DELTA survey's `--stamp` was a separate step, so a DELTA re-fired forever; the design was
  staged by hand into a folder the close-out gate later refused; the ADR number was
  `ls` + max + 1 (a recorded collision); and `/plan-swarm` still applied plans one line at
  a time with `apply-plan.sh` a year old. The judgement is entirely inside the five agents.
- **`plan-epic.sh`** runs the sequence with a state file (`.harness/run/plan-epic-<epic>.json`)
  that makes `--from <stage>` a resume. `MODE=interactive` exits 6 at the design gate and
  the DAG gate with the artefact's path and the `--from` that continues; `MODE=auto`
  self-approves and parks on the two things auto-accept never covers — an ABSENT
  specification and an open `decision`. A dispatch that hung, was denied, or returned no
  verdict is "could not judge" and approves nothing. Every dispatch is fresh; a planner
  revision carries the audit's findings by path. The survey's SPEC INDEX is staged as
  `spec-index.md` with `generated_sha`/`cites` frontmatter (what makes REUSE/DELTA
  mechanical); a DELTA stamps its baseline; the design is staged with a draft decision
  record per `DECISION:` line; `tk.sh adr-next` allocates the number once.
- **Agent contracts, made machine-readable.** The architect and planner put each open
  question on its own `DECISION: <question>` line and a missing requirement as
  `REQUIREMENT: <what>` with `ADEQUACY: ABSENT` first — the sequencer files them. The
  architect's "filing a requirement record" section used to show it running `tk.sh create`,
  a read-only agent writing the tracker; it emits the line now.
- **`stage-design.sh`** for `/design`; `/plan-swarm` applies with `apply-plan.sh`;
  `campaign.sh`'s per-epic prompt names the sequencer and reads the outcome from the
  return contract's first line. The skill went from 1,148 lines to 615.

- **mechanical** — re-stamp `harness.version`, when convenient.

### 0.10.27

**Deterministic steps out of the prose, part 9: the agent-side primitives.** One
config note: a stack may now declare `bootstrap.strategy: none`.

- **What was wrong.** The last layer of the audit: procedures every dispatched agent
  re-derived. The planner's "most important output" — the file-contention matrix — was
  built by eye (extract paths, grep, task × path per wave), and the recorded pile-up was an
  epic rated 11-wide with five wave-1 tasks on one module. The worker's mutation log had
  no transport to L2 — it lived in a worktree the sweep reclaims, so "spot-re-run three of
  sixteen" had nothing to spot-check. Seven reader agents each carried a paragraph asking
  the model to pass `--readonly` on every tracker call, a rule the dispatcher already knew
  from the frontmatter. The security lens was told to "read the invariants from the
  config" — a YAML file to find from a worktree and parse by eye. The spec-editor was
  warned, in prose, to anchor heading matches to line start (a recorded silent fold-in of
  nothing), to flip frontmatter, and to `git mv` a decision record at a number two streams
  once picked independently. `/harness-setup` ended with a stamp typed by hand and a
  worktree probe of four steps plus cleanup that was never idempotent.
- **`tk.sh validate <epic> --paths`** adds `contention.waves[].edges` above the
  dependency waves: every path two tasks of one wave both name, with its line count and
  the megafile flag, or a file both would create. `apply-plan.sh` runs it after the write
  and prints every edge; the planner resolves them (merge, serialise, split). The applier
  also refuses `update`/`delete`/`supersede`/`label`/`close` on a record that is
  `in_progress` or `closed` — §3c's rule, unchecked until now.
- **`lens-gate.sh`** finds the newest `mutate.sh` log in the checkout the change is in
  and names it in L2's prompt — or says none was found, which L2 weighs as a finding.
- **Readers are read-only by environment.** `dispatch.build_env` sets
  `TRACKER_READONLY=1` for a reader (`permission_mode == "default"`); `tk.sh` honours it as
  `--readonly`; a writer never inherits it (it is in `STRIPPED_FROM_CHILDREN`). The seven
  paragraphs are gone.
- **The security lens's checklist is injected** like the technology card:
  `security.invariants` verbatim under "Declared security invariants", or "declares none".
- **`staged.sh`** — `section <file> "<heading>"` (line-anchored; exit 1 absent, 2
  duplicated), `set-status <file> folded-in` (frontmatter in place, dated),
  `promote-adr <draft> --decision "…"` (`git mv` to `paths.adrs` at `adr-next`, status and
  decision filled in). The spec-editor calls them.
- **`check-project-config.sh --stamp`** writes `harness.version` from the plugin manifest
  in place. **`probe-worktree.sh [<lane>]`** is §7's probe as one call: scratch worktree,
  the init inside it, `.swarm-env` parsed and SOURCED in a fresh shell, identity and
  per-worker lines checked, the worktree removed whatever happened. Its first run found
  the harness's own stack declaring `symlink` to a `.venv-none` that never existed — every
  worker worktree on it would have refused to init — hence `bootstrap.strategy: none`.
- **Deferred, with the reason.** Four items of the plan's R9 need an environment the
  suite does not have and ship when it does: `fidelity-compare.mjs --measure/--widths/
  --states/--zoom` with `serviceWorkers: 'block'` (a computed-style diff table — needs a
  browser to test); `mutate.sh --dirty/--verify/--baseline` and the `class` column (needs
  the wavelab); `init-config.sh`, a `harness.yaml` skeleton from `project.detect_*`; and
  `check-invariants.sh` over `invariant_greps:` declared per framework, run by `brief.py`.
  None of these leaves a prompt telling an agent to run a bare sequence: the fidelity
  auditor and the mutation worker keep their by-hand steps, stated as such.

- **mechanical** — re-stamp `harness.version`, when convenient:
  `${CLAUDE_PLUGIN_ROOT}/harness/checks/check-project-config.sh --stamp`.
- **mechanical** — a stack whose bootstrap is a no-op declares `strategy: none`; one that
  said `symlink` to a directory that does not exist was never restoring anything.

### 0.10.28

**Headless campaigns could not dispatch since 0.10.21 — and the release A/B that found
it.** No config change.

- **What was wrong.** The orchestrator's sandbox excludes `dispatch.sh` so a nested
  `claude` can reach the keychain (Seatbelt cannot). That was the whole exclusion while the
  orchestrator typed `dispatch.sh` itself; from 0.10.21 it types `lens-gate.sh`, `fanout.sh`
  and `plan-epic.sh`, which dispatch on its behalf — inside its sandbox. Measured, on the
  first orchestrated wavelab run of 0.10.27: the survey dispatch under `plan-epic.sh` died
  in 73ms with "Not logged in · Please run /login" and the campaign stopped at §3a having
  run nothing ($1.23 of orchestrator, $0 of agents). Interactive `/swarm` and `/grind`
  were never affected — a terminal session has no sandbox around it. `resolve.py` now
  lists every script that dispatches (`DISPATCHING_SCRIPTS`) and a test pins the list to
  the modules that name `dispatch.sh` as an executable.
- **`worktree-sweep.sh` exited 1 silently** in any repository without `origin/HEAD`
  (a clone of a bare remote): the default-branch detection failed under `pipefail` before
  main/master/trunk were tried, and pre-flight read it as a failed sweep. Guarded, as
  `preserve-worktrees.sh` already was; the sweep's tests had all pinned `MAIN_BRANCH`.
- **A park commits and pushes its own state.** `plan-epic.sh` exit 4 leaves the gate, the
  `PARKED` note, the `decision` tasks it filed and the staged spec index in the tracker
  and the staging folder; the sequencer now syncs them (export → view → commit → push,
  as `halt.sh pause` does) and its report says so. Measured, the second orchestrated run:
  told only "parked — move to the next epic", the orchestrator spent 16 of its 26 turns
  reading `preflight.py`, `campaign_auto.py` and `tracker_sync.py` to decide what to
  commit. `--no-push` for a repository with no remote. The skill now states the rule for
  every exit: the report line is the whole answer; harness source is never read mid-run.
- **Three more from the same runs.** `ADEQUACY: **ADEQUATE**` (markdown emphasis) read
  as no verdict and cost a survey re-dispatch — the `ADEQUACY:`, `DECISION:` and
  `REQUIREMENT:` parsers now tolerate emphasis as `VERDICT:` already did. The architect,
  given a planned epic, looped `for id in …; do tk.sh show; done` — a compound command,
  denied identically on both attempts — so the sequencer now renders the epic's task view
  and hands the architect one file. And `campaign.sh` read a session that began
  `**stopped**` as *closed* because "Tasks closed: 0" appeared in its body; the outcome
  is read from the first line, as the contract says.
- **And two from the run after that.** The planner looped the same way as the architect
  (three of three attempts, $4.13 of planner for plans the sequencer rightly refused) — it
  gets the rendered view too. And exit 2 ("could not judge") said only "re-run once the
  cause is fixed": the orchestrator re-ran the stage three times unchanged, then parked
  and synced by hand in 13 turns. The report now names the choice — fix and `--from`, or
  file it and `halt.sh pause <epic>` as one call — and says a deterministic failure repeats.
- **And from the first run that reached a wave.** The lens gate's suite step ran in the
  primary at `main` — without the commit under judgement — and read green: `run.sh`
  resolves its checkout from `MAD_HARNESS_CALLER_PWD`, which the gate's own wrapper had
  exported as the primary, and `cwd=` alone did not move it (the R3 test used a fake
  runner that could not see an environment). L1 noticed the log's path. The suite now
  runs with the worktree in its environment too, and the test asserts it. The worker's
  prompt told it to `tk.sh close` its task while `/swarm` step 9 closes after the lenses
  — the worker did, and the orchestrator reopened it as a breach; the prompt and both
  writer agents now say never. L2, given no worker mutation log, was told to "run your
  own aimed mutants" — a reader with no Write tool authored a heredoc, was denied, and
  its round was void: it is now told to report `no mutation evidence` as a finding and
  judge by reading and re-running. A void lens's `NONE` line now carries the denials
  that voided it, with the remedy, and every lens prompt states the one-plain-command
  rule (measured: two rounds, $6.80, voided by `;`-joined status commands).
- **And from the first run that completed waves** (2 waves, 1 task closed, a second
  verified and merged; 112 orchestrator turns). Twenty-two of them polled a
  `plan-epic.sh` call the 10-minute Bash cap had backgrounded: it now runs detached
  through `fanout` (`--detach`, then `--wait <run-id>` until it stops exiting 5). Seven
  turns found that `merge-wave.sh` refuses a dirty tree and that the sequencer's staged
  design and spec index were committed on a park but not on success: it syncs on both.
  Eighteen turns parked by hand — four attempts at `tk.sh release`, the sync, the sweep, a
  run log, a commit, a push — where `halt.sh pause` is one call; the hard line now names it.
- **Two more from the run that produced the numbers.** `merge-wave.sh` refused every
  wave's merge on the tree pre-flight's own `autosync off` dirties (`.beads/config.yaml`)
  — the orchestrator committed the flag one run and set skip-worktree the next; paths the
  tracker owns are now named as residue and ignored, and anything else still refuses. And
  `campaign.sh` filed a session it had refused one `Read` as `stopped` although its first
  line said `**parked**` and the epic was parked: the outcome is the first line, whatever
  the exit code; `stopped` is a session that left none.
- **The measurement the series owed** — `harness/wavelab/ab.sh release --orchestrated`,
  the same seeded epic (3 tasks, 2 waves, an owner's settlement noted on it), one headless
  `campaign-orchestrator` per arm, one run each, one hour each. **n = 1 in a system with a
  measured 30× variance on identical tasks: the direction is a finding, the size is not.**

  | | 0.10.18 (`e2fa8d5d`) | this release (`d198885`) |
  |---|---|---|
  | orchestrator requests · tool calls | 87 · 166 (killed at the hour, mid-wave) | 86 · 85 (finished, 54 min) |
  | orchestrator cache-read tokens | 10.07M | 6.95M (**−31%**) |
  | orchestrator cost | ≈ $7.9 (from its transcript, same rates) | $5.40 (**−32%**) |
  | agents | $12.86 / 19 dispatches | $14.87 / 17 dispatches |
  | writers per run | $1.59 | $1.08 (−32%; the dispatcher-assembled prompt) |
  | lenses | 4 of 13 ended `ok:false` (denied) and their verdicts were accepted | 8 of 8 first-pass PASS, none void |
  | landed | 2 closed (both resting on a void L2), a 3rd in its 2nd round | 1 closed, 1 verified and merged, held by a permission record |
  | epic total | ≥ $20.8 (a floor) | $20.27 |

  What the orchestrator's hour was spent on is the finding the totals hide: ~90 by-hand
  mechanics before (21 `dispatch.sh`, 22 prompt files, 13 notes, 4 briefs, the merge,
  the slot, the claims, the lease, the checks) became 24 script calls after; the after
  arm spent the difference reading artefacts and, until the last two commits above,
  polling, `--help` and by-hand parking. The old orchestrator batched several tool calls
  per request, so requests fell far less than calls. The epic's total cost did not move
  — the lenses now run to completion (L2 spot-re-runs mutants instead of being voided)
  and cost what they cost — while what it bought did: no close rests on a verdict a
  denied lens wrote. Fifteen defects were found by the rig before the two comparable
  runs existed, all in the headless path nothing but a live orchestrator exercises;
  each is above with its test. What is still owed: runs, not one — `--runs 3` on both
  arms, and the same on a TipDonkey epic where the orchestrator's context is 3–4× the
  lab's and each request costs accordingly.

- **mechanical** — re-stamp `harness.version`, when convenient:
  `${CLAUDE_PLUGIN_ROOT}/harness/checks/check-project-config.sh --stamp`.

### 0.10.29

**§3 at strong or lower, with escalation — a moved cost default, in its own release.**
One config note: `dispatch.plan_tiers` (on by default; `false` restores every declared
tier).

- **§3 does not redo what is done and unchanged, and tiers its stages by the epic's
  declared surface, with escalation.** Planning the 3-task lab epic took 29 minutes and
  $7.33 against $1.07 and 3.5 minutes of building; a pre-step does not get quicker for a
  smaller epic on its own — ~90% of each stage's output tokens were thinking at the
  tier's fixed effort, and the visible design (1,621 words) was ~11% of them. Two rules,
  both the owner's:
  - *Reuse.* A design staged while the spec index reads REUSE is reused (`ARCHITECTURE:
    reused`). A plan is reused only when the epic carries an `AUDIT: PASS` for **this**
    task set — the audit note now records a fingerprint of the tasks' ids, titles,
    descriptions and acceptance — and the spec index reads REUSE (`PLAN: reused`). Never
    on the triage word, never on a task count, never a mechanical check standing in for
    the audit's judgement.
  - *Tier from the surface.* `models/complexity.py` reads what the project declared: the
    paths the epic and its tasks name (existing, with line counts; new), the `areas`
    they fall in and whether one carries a trigger, `security.paths`/`tokens`, megafiles,
    contention edges. Three readings: **flagged** (something the project marked),
    **simple** (tasks naming paths, nothing flagged), **unreadable** (no tasks, or none
    naming a path — a new epic, usually). The card is computed before the architect and
    again before the audit (from the design and the plan too), and noted on the epic.
    The owner's rule, **on by default** (`dispatch.plan_tiers: false` restores every
    declared tier): the survey at `worker` as before; the
    architect (sanity-check or design) at `strong` unless the surface is flagged —
    unreadable included, since it can escalate itself; the audit at `worker` when the
    surface reads simple, else its declared `strong`; the planner never moved. A stage
    run lighter may answer `ADEQUACY: ESCALATE — <why>` / `VERDICT: ESCALATE — <why>`
    and is re-run once at its declared tier with the reason; at the top tier, ESCALATE
    parks.
  Measured, §3 alone on the READY lab epic (`ab.sh plan_tiers --plan-only`, two clean
  runs per arm): the sanity-check at strong vs strategic — 150/95 s vs 175/184 s, output
  10.8k/6.2k vs 12.8k/10.8k tokens, $0.48 vs $0.64 median — cost and time −27%, output
  −42% (the only spread that separates at n = 2). The default moved on the owner's
  decision ("strong or lower, with escalation"), the stated exception to
  measured-before-moved — and the second series then sized the whole rule: §3 in full
  (survey → sanity-check → planner → audit) on the never-audited lab epic, three clean
  runs per arm, all PLANNED, no denials, no escalations:

  | stage | declared | default | seconds (median) | cost (median) |
  |---|---|---|---|---|
  | survey | worker | worker | 82 → 103 (noise) | $0.19 → $0.19 |
  | sanity-check | strategic | strong | 146 → 112 (−23%) | $0.63 → $0.54 (−14%) |
  | planner | strong | strong | 143 → 185 (noise) | $0.71 → $0.73 |
  | audit | strong | worker | 147 → 64 (−56%) | $0.65 → $0.19 (−71%) |
  | §3 per run | | | 8.4 → 8.2 min (overlap) | **$2.43 → $1.63, −33%, spreads separate** |

  The same §3 on the same epic cost 29 minutes and $7.33 before this release's
  proportion, rendered-view and one-command fixes (both arms carry those); the tiers
  took the cost down a further third. Two caveats: the epic is trivial, so both arms'
  audits passed 3/3 — this sizes the audit's cost at `worker`, not its catch rate,
  which needs a plan with a real flaw in it; and escalation fired zero times, so the
  path is pinned by the suite and not yet seen in the field.
  The lab seed carries acceptance in the record's field so the lab epic triages READY.
  Every §3 prompt states the epic's size and ends with the one-plain-command rule (two
  of four surveys in the first series were refused a compound command).
- **§3 writes in proportion to the epic.** The time was the output, and the output was
  the template: for an epic whose whole source was 239 words the architect wrote a
  1,621-word design (20k output tokens, 273 s), the planner a 3,876-word plan for three
  tasks that already existed (28k, 342 s), the audit 17k tokens about it, at ~1,300 output
  tokens a turn — while the survey, which had little to index, took 75 s. Each sequencer
  prompt now opens with the epic's task count and what a proportionate deliverable is
  (confirm or flag, a paragraph per task; emit only what changes; one line per task), and
  the architect's, planner's and analyst's contracts say ≤ 2 pages is a ceiling, not a
  target. Measured next by `ab.sh plan_tiers --plan-only`, whose both arms carry this.

- **mechanical** — re-stamp `harness.version`, when convenient:
  `${CLAUDE_PLUGIN_ROOT}/harness/checks/check-project-config.sh --stamp`. Nothing to set
  unless you want the declared tiers back: `dispatch: {plan_tiers: false}`.

### 0.10.30

**Project-owned model config: redefine tiers, add tiers, extend or redefine providers.**
Two config notes: the per-agent override block is renamed, and four blocks are new.

- **What was wrong.** Routing a tier at a non-Anthropic model (OpenRouter, DeepSeek, a
  local endpoint) — to cut cost and to measure whether a cheaper model holds up — meant
  patching `harness/models/tiers.yaml` inside the plugin cache, which no consumer can
  do. Now a project's `harness.yaml` patches the definitions: `tiers:` per tier per key
  (leave out what you do not change and the plugin's value stands, so an upgrade still
  reaches you), `providers:` per name with `env` per key, `default_tier:` and `ladder:`
  replaced wholesale. The merged config is what gets validated. Two questions, one word
  each: `agent_tiers:` says which tier an agent runs on; `tiers:` says what a tier is.
- **The refusals.** A tier patch that sets `model` without `provider` (restating
  `anthropic` is fine, and is the point); a literal credential in a project provider
  block (`${VAR}` references only — the file is committed); a ladder naming a ghost, a
  rung twice, or leaving a defined tier or the policy-forced tier off it. The three
  tiers.yaml rules — no model in a provider env, concrete model ids, no bare alias — now
  hold on the merged config. All four new keys are the owner's: no agent may write them.
- **Redefinition is loud.** You may redefine `strategic`, the tier high-risk work is
  forced to; the protection is visibility, not prevention. Every dispatch record carries
  `tier_source: plugin|project`; `make models-cost` never shares a row between the two and
  `ab-report.sh` refuses an arm that mixes them; `check-project-config.sh` prints each
  redefinition beside what the plugin ships and names a redefined `strategic`;
  `dispatch.sh --dry-run` says `(redefined by project)` and prints the effective route.
- **The frontmatter mirror** (`check-model-config.sh --write`) stamps `model:`/`effort:`
  only for Anthropic tiers — the frontmatter reader has no provider concept — and prints
  the exemption for any other.

- **Two merge defects the release's lab run found.** beads' own hooks (`core.hooksPath =
  .beads/hooks`) *stage* the export on every write, and git will not merge over a staged
  entry it must set back to HEAD's blob: two branches that never touched
  `.beads/issues.jsonl` were refused — and `merge-wave.sh` filed the refusal as "CONFLICT
  in unknown paths", a planning miss, on both. A merge that fails with no conflicted path
  is now reported with git's own words and stops the wave (the cause is shared); staged
  tracker residue is unstaged before the merge, and `commit.sh` unstages a worker's
  pre-staged export instead of refusing it as a contaminated index it was told never to
  reset.
- **mechanical** — rename the per-agent override block, if you have one: `tiers:` →
  `agent_tiers:`. No consumer had one when the rename shipped; the old key now means the
  definitions block, and a map of agent names under it fails the config check by name.
- **mechanical** — re-stamp `harness.version`, when convenient:
  `${CLAUDE_PLUGIN_ROOT}/harness/checks/check-project-config.sh --stamp`.
- **ask the owner** — whether to route a tier at another provider. The worked example
  is commented out in `templates/harness.yaml.example`; `models/probe-compat.sh <name>`
  first; then one A/B under `make models-cost`, reading `tier_source`.

### 0.10.31

**Agent teams: assessed, tried, measured, and not adopted.** No config change.

- **The assessment.** Would Claude Code's agent teams change the harness? No — recorded in
  `docs/concepts/architecture.md` with the checklist for revisiting. Teammates do not spawn
  in `-p` or SDK sessions (the campaign, the wavelab and CI are all unattended); a teammate
  inherits the lead's effort, so tiers with different effort cannot coexist; no per-teammate
  ceiling, cost record, sandbox or permission mode at spawn is documented (prompts go to the
  lead for a person to answer); a teammate loads its definition's tools and model but not
  its skills. The one property teams have that the Agent tool lacked is that a teammate's
  output reaches the lead only by message — the measured reason `guard-agent-tool.sh`
  exists. That pointed at one possible use: an interactive design debate.
- **So it was built and measured**, rather than argued about: `/design-debate`, an architect
  and an analyst spawned as teammates who argue a design until the analyst passes it. Both
  arms ran against the same seeded lab epic.

  | | `/design` + one analyst audit | `/design-debate` |
  |---|---|---|
  | lead session | 30 requests, 8 min, ~$12.45 | 19 requests, 10 min, ~$12.01 |
  | agents | $1.39 — two **recorded, capped, sandboxed** dispatches | ~$21.02 — **no event, no ceiling, no sandbox** |
  | **total** | **~$13.84** | **~$33.03 (2.4×)** |
  | design | 2,102 words | 1,772 words, 2 rounds |
  | audit | `VERDICT: PASS`, 0 blocking, 3 filed | round 1 FAIL (1 blocking) → fixed → round 2 PASS, 4 filed |

  (Lead and teammate figures are estimated from their transcripts at list rates; the two
  dispatch figures are the SDK's own accounting. Not the same method.)

  The debate's blocking finding was real but was a defect **its own architect introduced**
  — the fold-in routing named a kind of destination rather than a path — and `/design`'s
  architect had named a concrete path and never had it. Both arms ended with a design that
  passed its audit. One run each: the difference between two drafts is variance, not a
  property of the method. The cost difference is not: a teammate carried ~45k tokens per
  request against a dispatched agent's ~18k (a full session — CLAUDE.md, the skills listing,
  the backend's SessionStart hook — where the boundary gives a lean catalog and doctrine in
  a cached system prefix) and took ~2.5× the requests. And arm B wrote **zero**
  `harness.dispatch` events, so `make models-cost` and every A/B series were blind to $21 of
  it — the accounting gap the assessment predicted, measured.
- **Removed on that evidence.** `/design-debate`, the guard's exemption and `doctrine.sh`
  are gone; `guard-agent-tool.sh` refuses every one of this plugin's agents again, without
  exception, and a test pins that no environment changes it. `/design` is unchanged and
  remains the way to design.
- **Kept: `wavelab/drive-interactive.py`**, which is not teammate-specific — it drives a
  real `claude` session in a pty and answers the TUI's dialogs, and is the only way to
  exercise an interactive command (`/design`, `/requirements`, `/decision`) in the lab at
  all. It is what made the two arms above comparable.

- **mechanical** — re-stamp `harness.version`, when convenient:
  `${CLAUDE_PLUGIN_ROOT}/harness/checks/check-project-config.sh --stamp`. Nothing to set.

### 0.10.32

**A tier off Anthropic declares its price, and every record says which number it is.** One
config note: `price` in a tier, required off Anthropic.

- **What was wrong.** Routing the `worker` tier at DeepSeek V4 Pro exposed that `cost_usd`
  on every dispatch record is the SDK's `total_cost_usd` — Claude Code pricing from its own
  table. On Anthropic that is the vendor's accounting and right. Against a third-party
  endpoint it is a fiction, and a plausible-looking one: measured three times, a flat
  **$5.00 per Mtok of input** for DeepSeek (35,335 tok → $0.17675) against its published
  $0.66 off-peak / $1.32 peak. That number flows into `make models-cost`, `ab-report.sh`
  and the `tier_source` split as if real.
- **`price` on the tier** — `input_per_mtok` and `output_per_mtok` required,
  `cache_read_per_mtok` / `cache_write_per_mtok` optional (defaulting to the input rate,
  never free), and `off_peak_multiplier` with `peak_utc` windows, because DeepSeek charges
  half outside 01:00–04:00 and 06:00–10:00 UTC on weekdays and assuming either way is a 2×
  error. `_validate` refuses a tier off Anthropic that declares none, by name.
- **`cost_source` on every event** — `sdk` or `priced (window: rates)`. `make models-cost`
  never shares a row between the two and `ab-report.sh` flags an arm that mixes them, as it
  flags mixed code.
- **The ceiling is NOT fixed by this, and knowing so matters**: `--max-budget-usd` is
  enforced by the CLI against its own estimate, so on a provider it over-prices 4–8×, a
  tier's ceiling bites 4–8× sooner than the number says — a worker killed mid-task that
  looks like the model failing. Bound such a tier with `task_budget_tokens`.

- **`billing` on the provider — which pocket, because both are real.** `subscription`
  where the work draws on a plan's allowance, `metered` (default) where it is billed per
  token. The SDK's figure for an Anthropic dispatch on a Max plan is not "free": it is the
  dollar amount of allowance consumed, and the allowance is finite — the work stops when
  it is gone. But it is not a metered dollar either, so `make models-cost` and
  `ab-report.sh` print the two totals APART and never sum them; a sum would state a number
  neither pocket paid. The plugin declares nothing: an API key is metered and a plan is
  not, and only the operator knows which they are on.

- **mechanical** — nothing, unless you route a tier off Anthropic (declare its `price`, or
  the config check fails with the reason) or you are on a plan rather than an API key
  (`providers: {anthropic: {billing: subscription}}`, so the two pockets are reported
  apart).

### 0.10.33

**A worker tier off Anthropic, measured.** No config change.

- **The series.** `worker_provider`, 2 runs per arm on the seeded lab epic (wave 1, two
  tasks, fan-out 2), both arms judged by the four lenses. The `off` arm is the plugin's
  worker tier, `anthropic/claude-sonnet-5`. The `on` arm redefines that one tier in the
  lab project's `harness.yaml` as `deepseek/deepseek-v4-pro`, effort high, ceiling $3,
  priced from its published rates. **Every other agent is unchanged**: the planner and the
  four lenses stay on Anthropic in both arms, so this measures a worker swap, not a system
  swap.

  | per run, median (IQR) | Sonnet workers | DeepSeek workers |
  |---|---|---|
  | workers | **$1.56** ($1.29–$1.83) subscription | **$0.15** ($0.12–$0.18) metered |
  | subscription pocket | $6.59 ($6.26–$6.92) | $5.12 ($5.09–$5.15) — **−22%, spreads separate** |
  | metered pocket | — | $0.15 — new spend, not a delta |
  | agent minutes | 25.5 (24.6–26.3) | 29.2 (26.7–31.6) — **+14%, spreads separate** |
  | turns / dispatch | 14 (12–18) | 16 (12–21) — spreads overlap, not a finding |
  | wave gate | GREEN 2/2 runs | GREEN 2/2 runs |
  | first-pass L1 · L3 · L4 | 4/4 · 4/4 · 4/4 | 4/4 · 4/4 · 4/4 |
  | first-pass L2 (test quality) | 1/4 | 0/4 |

  **The workers are ~10× cheaper and the wave still lands.** What that buys at the wave
  level is smaller than it looks: the workers were 24% of the run, so moving them saves 22%
  of the subscription pocket and adds $0.15 of invoiced spend. The rest is the lenses, which
  are Opus in both arms — on this shape of work, **judging costs more than doing**.

- **Quality: the same verdict, and the same complaint.** L1, L3 and L4 passed everything in
  both arms. L2 failed 7 of 8 tasks across the series — 3 of 4 with Sonnet, 4 of 4 with
  DeepSeek — and every one of those failures names the same two things: a character class
  the tests do not pin (`value.strip()` narrowed to `value.strip(" ")` survives every test),
  and no mutation evidence. At n = 2 per arm the L2 difference (1/4 vs 0/4) is one task and
  is not a finding; what the series does show is that the gate catches the same defect in
  both arms, which is the property that makes a cheaper worker safe to consider at all.
- **An artefact, named.** The series ran on a tree frozen before `mutate.sh` learned to load
  the worktree's environment itself, so mutation testing was unreachable from a worker's
  sandbox in **both** arms — part of every "no mutation evidence" finding is the rig, not
  the worker. Every L2 FAIL also carried at least one blocking finding that was not about
  mutation, so no failure turns on the artefact alone.
- **The ceiling still does not bite correctly off Anthropic** (0.10.32): `--max-budget-usd`
  is the CLI's own estimate. The DeepSeek workers ran at $0.03–$0.13 against a $3 ceiling,
  so it never came near — but a longer task would be killed early, and `task_budget_tokens`
  is what bounds it.

- **Three defects the run surfaced, each fixed with a test.** `VERDICT: PASS` inside an
  inline code span read as no verdict — the fifth spelling of the bug 6175a37 fixed, found
  by re-reading all 25 lens answers the series kept rather than one more time by hand.
  `tk.sh render <epic> --check` crashed: the flag read `--write`'s destination, which is
  `None` when nobody asked to write, so the drift gate the generated banner names could
  never be run — the path now travels with the flag. And `ab-report.sh` printed a
  `cost / run` headline that **summed the two pockets** and computed its delta from it, the
  one thing 0.10.32 said a report must never do; cost per run is now per pocket, and a
  pocket only one arm spends from is reported as new spend rather than a percentage.

- **mechanical** — nothing, unless you route a worker tier off Anthropic; then 0.10.32's
  `price` block applies. Re-stamp `harness.version` when convenient:
  `${CLAUDE_PLUGIN_ROOT}/harness/checks/check-project-config.sh --stamp`.

### 0.10.34

**`max_budget_usd` is enforced against what the dispatch actually costs.** No config change.

- **What was wrong.** `--max-budget-usd` is checked by the CLI against its own price table.
  On Anthropic that is the vendor's accounting and right. On a tier routed elsewhere the
  CLI is pricing a model it does not know — measured at a flat $5.00/Mtok of input for
  DeepSeek — so a $3.00 ceiling bit at roughly $0.40 of real spend: a worker cut off a
  fifth of the way into its task, looking for all the world like the model failing. 0.10.32
  named this and did not fix it.
- **The harness meters a priced tier itself.** `dispatch.sh` already streams the agent's
  messages, and each one carries its own `usage` — so the real cost is added up as it
  arrives, at the tier's declared rates, and the dispatch is stopped when IT passes the
  ceiling. The kill has the same shape the CLI's produces, so everything that routes a
  budget kill keeps working. `ceiling_source` on every event says who checked it:
  `harness`, `cli` (Anthropic, where the SDK's figure is right), or `none`.
- **The CLI is given no ceiling at all for a priced tier.** It checks that flag against
  its own price table, which for a model it does not know is a fiction — so its kill lands
  at a real-dollar figure nobody can state, and a threshold in an unknown currency is not
  a bound. This shipped a loosened "backstop" first (10x, then 25x); at 10x it was close
  enough to race the meter it was meant to back up, and the fix was not a bigger number.
  One enforcer, in known units.
- **What the backstop was really for is now caught twice, in terms the harness can state.**
  `probe-compat.sh` gained a **streamed token accounting** probe: the ceiling is enforced
  from the usage on each message as it ARRIVES, which is a different payload from the
  final one the existing probe read — a provider can report exact totals at the end and
  nothing on the way. It is **advisory, not fatal**: losing the ceiling is not a reason to
  refuse a provider whose cost records are exact, so it prints `WARN` and names
  `task_budget_tokens` as what stands in. And at runtime, a priced dispatch that has run
  three turns without a single usage payload is **stopped** —
  `terminal: unenforceable_ceiling`, which is not `budget` because nothing was exceeded:
  it is a configuration fault to fix, not a task to split.
- **Three things measuring it against a live endpoint corrected**, none of which the unit
  tests could have found:
  - **One API response arrives as several messages** — a thinking block, then a tool-use
    block — each carrying the *same* usage and the same `message_id`. Ten messages for what
    the result message counted as five turns. Summing them as they arrive doubles the cost
    and the turn count, so the ceiling fires at half the spend it names. Deduplicated on
    `message_id` the totals are exact: 13,378 input and 53,120 cache-read, against the
    result message's own 13,378 and 53,120.
  - **A streamed usage reports `output_tokens: 0`** — the real figure (682) only arrives in
    the result message, which a killed dispatch never gets. So the meter prices prompt
    tokens only and says so, and the ceiling is reached a little late, never early: on the
    measured sample the output was 11.9% of the cost. The alternative was to estimate
    output from the text, which is exactly the kind of plausible number this module exists
    to keep out of the record. A dispatch that COMPLETES is still priced in full.
  - **The two spellings of the cache counts.** The SDK's result says
    `cache_read_input_tokens`, the meter records `cache_read_tokens`; reading only the
    first reported a metered kill as 0% cache hit on a worker that had read 53,120 tokens
    from cache.
- **Also corrected**: the one-line dispatch summary printed the SDK's `$0.0000` next to
  `terminal=budget` for a dispatch that had just spent $0.009, and the kill message printed
  "$0.01 spent against a $0.01 ceiling" at two decimals. Both now print the number the
  ceiling was actually checked against.

- **How far off the CLI's figure is, measured**: twice against DeepSeek off-peak, it
  reported $0.1600 against $0.015494 of real cost (10.3x) and $0.1105 against $0.011348
  (9.7x). The 4-8x in the 0.10.32 note was at peak rates, where the real price doubles.

- **mechanical** — nothing. Re-stamp `harness.version` when convenient:
  `${CLAUDE_PLUGIN_ROOT}/harness/checks/check-project-config.sh --stamp`.
