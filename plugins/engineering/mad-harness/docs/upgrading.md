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
