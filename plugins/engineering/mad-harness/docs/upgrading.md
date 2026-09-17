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
