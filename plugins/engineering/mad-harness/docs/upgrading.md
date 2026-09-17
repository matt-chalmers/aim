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
