---
name: harness-setup
description: Initialise, upgrade or repair this harness in a repository — write harness.yaml, pick the stack modules, and verify the result end to end. Invoke it when harness.yaml is missing, when check-project-config reports UPGRADE (the plugin moved on since the config was reviewed), when a check reports the config disagrees with the repo, or when adding support for a toolchain the harness has no module for.
---

# Harness setup

The harness is portable; `harness.yaml` and `harness/stacks/` are what make
it about *this* repository. This is how to write them.

**Work through it with the owner, one decision at a time.** Do not generate a
plausible config and present it as done — a wrong `slug` silently names every
worker database, and a wrong `security` surface silently stops a lens firing.

## 0. Initialise, or upgrade?

If `harness.yaml` already exists at the repository root, this is an **upgrade**, and most
of what follows does not apply: never rewrite a block the owner already settled. Start
from what the check says:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/checks/check-project-config.sh   # the `harness:` line names the stamped and installed versions
```

Then read `${CLAUDE_PLUGIN_ROOT}/docs/upgrading.md` and apply every version section newer
than the stamp, **oldest first**. An unstamped config is older than all of them. Each item is
tagged: **mechanical** — apply it and say what you did; **ask the owner** — one question,
carrying whatever the repository already answers, exactly as §1 does for a fresh config.

When the last section is applied, stamp and re-check in one call:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/checks/check-project-config.sh --stamp   # writes harness.version from the plugin manifest, then checks
```

It writes the installed version into `harness.yaml` in place — read from the manifest,
never typed, because a mistyped stamp is a config that reads as current while missing every
block the version reads — and must end `OK` or `WARN`, never `UPGRADE`. Then §7, because an
upgrade that validates but breaks a worker's worktree has not been tested either.

A fresh repository has no config: continue with §1.

## 1. Find out what is already true

Do not ask what you can read. In one `scan.sh` call:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/verify/scan.sh -e 'pyproject.toml|package.json|go.mod|Cargo.toml|Gemfile' \
                       -e 'pytest|vitest|jest|go test' \
  && ls -d */ && cat .beads/config.yaml 2>/dev/null
```

The repository already answers most of the config: which toolchains it uses, where
its docs live, what its top-level areas are. Bring the owner the *gaps*, not a
questionnaire.

## 2. Write `harness.yaml`

| key | what it is | how to get it wrong |
|---|---|---|
| `name`, `slug` | display name; lowercase id for per-worker resources | a slug with a hyphen or space breaks database names |
| `harness.version` | the plugin version this config was reviewed against; `check-project-config.sh --stamp` writes it | typing a version instead of reading it from the plugin manifest — a wrong stamp hides an upgrade |
| `stacks` | which modules under `harness/stacks/` apply | naming one whose `detect` files are absent |
| `tracker` | which task backend this project uses | omitting it is fine — that means `beads`, the default |
| `beads.prefix` | what this repo's issue ids start with | guessing — read it off `${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh list` |
| `paths` | docs, index, adrs, proposed, features, architecture | pointing at a directory that does not exist |
| `areas` | path → label, in report order, with `triggers` | leaving out an area, so its changes group under `other` |
| `security` | paths, tokens and invariants that fire the security lens | **the highest-consequence entry — see §4** |
| `testing` | layout, gates, coverage, aggregate commands | none; it is rendered into `test-doctrine` |
| `swarm.merge_slot` | the task id workers take before committing | — |

## 3. Which task tracker — ask, but lead with the default

Every project has tasks, so unlike the other axes this one has a **default rather than an
absence**: omit the `tracker:` block entirely and the project gets `beads`, which is what
every project used before the backend was swappable. Do not raise it as a decision unless
the owner has a reason to care.

| | `beads` | `mdfiles` |
|---|---|---|
| needs | the `bd` binary installed | nothing external |
| records | an embedded database | one markdown file per open task |
| the tracked artefact | `issues.jsonl` — one blob | per-task markdown, so a wave's changes **diff reviewably** |
| record ceiling | ~64KB, failing **closed and silently** | none |
| extras | `prime`, molecules (`ready --mol`) | — |

**The reason to choose `mdfiles` is the diff.** A wave's tracker changes become something
a human can read in review, where `issues.jsonl` is a blob whose changes read as noise.
The reason to stay on `beads` is that it is proven here and has the richer feature set.

**Whichever they choose, the hot store must be gitignored in THEIR repository.** The
harness ignores its own; a consuming project has to add the same two lines, or the first
wave commits every worker's live claim files and half-written records:

```gitignore
.claude/worktrees/ # one per dispatched worker; `git add -A` otherwise commits them
                   # as embedded git repositories
.harness/cache/    # toolchain caches, kept inside the sandbox boundary
.harness/run/      # claims, the merge-slot lease, telemetry
.harness/tasks/    # mdfiles only: the live records
.swarm*            # per-worker scratch a worker writes at ITS worktree root (.swarm-env
                   # and whatever a version adds); ignored, it never makes a worktree
                   # read as dirty, so the sweep can always reclaim it
```

**If they choose `mdfiles`, `export` is not optional.** The hot store is gitignored — it
has to be, because workers write it during a wave and a tracked file written mid-wave
leaves the primary checkout dirty and fails the next wave's pre-flight. Without an export
path the backlog would live only on the machine that ran the wave. `make project` refuses
the config rather than letting that ship.

```bash
${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh backend --json     # what the configured backend can do
make conformance                               # prove it against the real binary
```

## 4. The security surface deserves a real conversation

`verifier-security` answers *"what can the wrong person now reach?"*, which nobody
can answer from the code alone. Ask the owner directly:

- Which paths, if changed, always warrant a security look?
- Which identifiers signal sensitive data — the columns, headers and tokens?
- What must never happen, stated as a rule? (*"Never expose X to another user."*)

**A missing invariant is not a warning, it is a lens that never fires.** If the
owner is unsure, capture what they *do* know and record the gap rather than
inventing a plausible-sounding rule.

## 5. Two axes: toolchain and framework

**Stacks answer "how do I RUN things"; frameworks answer "how do I WRITE good code".**
They are independent — Python+uv could be Django or FastAPI, and `uv sync` has nothing
to say about the N+1 query. A harness with only one axis cannot serve the second
project it meets.

| the fact | axis |
|---|---|
| how to install dependencies, run tests, isolate a worker | **stack** |
| what a good change looks like, what must never be done | **framework** |

Both live in `harness/stacks/` and `harness/frameworks/`, both are named in
`harness.yaml`, and both may be shadowed by project-local copies under `.harness/`.

### The context budget — read this before writing either

Each module carries a **card** (a handful of rules, injected into the prompt of every
dispatch in its lane) and optionally a **doctrine skill** (the depth, loaded only when
an agent asks). The split is a cost decision, not a style one:

- a card costs ~250 tokens and **no tool call**, on every dispatch in that lane
- a doctrine skill costs **nothing** until invoked
- a *preloaded* skill costs its full size on **every dispatch in every project**

So: a rule earns a card slot only if getting it wrong is both likely and expensive.
Everything else goes in the skill. **Never add a `stack-*` or `framework-*` skill to
an agent's `skills:` frontmatter** — that converts a free module into a permanent tax
for every project, including those not using that technology. `make skills` fails if
you do.

## 6. Pick or write the stack modules

Check `harness/stacks/` first. If the toolchain is there, name it and move on. If
not, a new module is one YAML file — the harness needs no code change:

```yaml
name: <toolchain>            # MUST equal the filename
root: "."                    # the module names NO location; the project overrides it
detect_any: [<lockfile>]     # the marker for THIS TOOLCHAIN — any one is enough
detect_language: [<manifest>]  # the looser marker; matching only this is AMBIGUOUS
dependency_dir: <gitignored dir a fresh worktree lacks>   # relative to root
bootstrap:
  strategy: install | symlink
  command: <restore command>   # install only
  cwd: <subdir>                # install only
env:
  SOME_VAR: "{slug}_w{worker}"  # per-worker isolation
commands:
  test: <whole suite>
  test_scoped: <suite for one path> {path}
  verify: <the cheapest command that proves the runner still works>
  # lint / typecheck / format_check as your toolchain provides
```

**Name the toolchain's own marker, not the language's.** `pyproject.toml` is shared by
uv, Poetry, PDM, Hatch and plain pip; `package.json` by npm, pnpm, yarn and bun. A marker
that identifies only the language lets another tool's project pass config validation and
fail later inside a worker's worktree, mid-wave — where the escalation policy reads it as
the worker's fault rather than the config's. Put the lockfile in `detect_any` and the
manifest in `detect_language`, and an ambiguous repo is reported rather than assumed.

**Say where it lives in `harness.yaml`, not in the module.** A bare `python-uv` means the
root; `{name: python-uv, root: backend}` relocates it and every derived path with it.

**Give it a `verify`, and make it cheap.** This is the command the pre-flight probe runs
to decide whether the others still work — a collection pass, a `--version`, a dry run.
Something that loads the runner and its config without doing the work: the reference one
is measured at ~3s against a suite that takes over two minutes to execute.

It earns its place because a wrong command fails **loudly but late** — once per worker per
wave, and a worker that gives up returns `BLOCKED`, which the escalation policy sends to a
costlier tier that cannot fix a config error. A module with no `verify` is reported
UNPROVEN rather than assumed good, which is a real answer and a fine place to start.

**The runner is a project choice, not a toolchain property.** A package manager does not
decide which test runner you use. So the module ships a default and `harness.yaml`
overrides it — and `${CLAUDE_PLUGIN_ROOT}/harness/checks/check-stack-commands.sh --repair` writes that override
for you when the default has rotted, deriving a replacement from what your repository
already declares and proving it by running it.

**Choose `strategy` on evidence, not habit.** `install` when restoring is cheap
(a package manager that hardlinks from a global cache); `symlink` when it is
expensive and concurrent readers are safe (a large `node_modules`). Getting this
backwards costs minutes per worker, every wave.

**Every stack that needs per-worker isolation must declare it in `env`.** A
worker without its own database or build directory silently shares one with its
siblings, and the suite still passes — which is why nobody notices.

### Domain vocabulary — the guard nobody thinks to ask for

List the nouns that mean something in your product and nothing in anyone else's under
`domain.nouns`. It is not documentation; it is what the prompt guard checks against, so
an empty list means no check.

**Only unambiguous words.** A noun that also means something in software generally will
fire on ordinary prose — seeding `fixture` here cost 41 false hits, because it is a
product entity in one domain and a test construct in every codebase. Same trade as
`detect_any` versus `detect_language`: the specific word, never the shared one.

**Why it matters more than it looks.** An agent that names one of your
entities in a project that has no such thing is confidently wrong — not an error, advice. Four reviews found
that class by hand before this list existed, and a fix for one finding reintroduced it
six times over in the block that gets mirrored three ways.

### Design fidelity — declare it or leave it out

If your project has a design handover to compare screens against, add a `fidelity:`
block **and mark the lanes that do that work with `fidelity: true`**; if it does not,
**omit both** and the tooling stays dormant.

It is lane-activated for the same reason stacks are not: a stack is what the repository
is built with, so a worker may touch it at any time, but fidelity is a distinct activity
a lane either does or does not do. Preloading its doctrine onto the generalist writer
charged every backend and docs dispatch ~890 tokens for advice it would never read. Leaving it
out is the right answer for most projects, and it is what keeps a worker from being
handed a command it cannot run.

A fidelity-using project must also provide `playwright` and `sharp` in its Node
dependencies — the harness installs nothing and borrows them from your project.
Credentials stay in the environment (`FIDELITY_USER`, `FIDELITY_PASS`); they have no
default and no home in the config file.

### One thing config cannot do for you

`testing.aggregate_commands` names the runner your wave gate uses, but a **permission
rule is not config** — it lives in each orchestrator command's `allowed-tools`, and it
ships granting `Bash(make:*)`. If your aggregates use a different runner, add it there
too. A command matching no rule does not fail; it stops on a prompt that, in an
unattended dispatch, surfaces in the orchestrator's session and waits indefinitely.

## 7. Verify, and do not stop at the first green

```bash
make project        # config valid, stacks present, hard-coded sites agree
make skills         # declarations resolve, preload bills within budget
make models         # every agent's tier resolves
make commands       # each stack's declared commands actually work
```

**These four, plus the worktree probe below, are the consumer's verification — the whole
of it.** The harness's own test suite (`make harness-test` in the plugin's repository) is
the plugin author's: it asserts the harness's internal invariants over the files it
tracks, with `git ls-files` and `git show HEAD`, and an installed plugin cache is not a git
repository. Run from there it fails fifteen tests that say nothing about your
configuration; a consumer has no business validating the harness's internals, and the
suite refuses to run outside a checkout rather than fail confusingly.

`make project` must end `OK` or `WARN`. `UPGRADE` means the stamp is missing or behind: §0
was skipped, or the version was typed rather than read.

`make commands` is the one that will fail first on a fresh config, and its failure is
usually a real finding rather than a typo: it means the command you declared is not the
command this repository runs. Re-run it as
`${CLAUDE_PLUGIN_ROOT}/harness/checks/check-stack-commands.sh --repair` and let it derive one.

Then prove the part no check can — one call:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/swarm/probe-worktree.sh <lane>       # scratch worktree → init inside it → source .swarm-env → identity + per-worker lines → remove
```

It creates a detached scratch worktree where the dispatcher puts a worker's, runs the init
from inside it, sources the `.swarm-env` it wrote in a fresh shell (the unquoted value that
reads fine and fails a real shell is caught here, not mid-wave), checks `TRACKER_ACTOR`,
`SWARM_LANE` and every per-worker variable the stacks declare, and removes the worktree
whatever happened (`--keep` leaves it for inspection). A config that validates but produces
a worktree a worker cannot use has not been tested; on its first run this probe found the
harness's own stack declaring a restore of a directory that never existed. A project whose
stacks declare no per-worker variable is told so rather than passed — decide whether that
is right for its stacks.

## 8. What stays project-owned

`CLAUDE.md` — **optional**. Your conventions, domain terms and what to avoid. The harness
requires nothing from it: the rules it enforces on tasks and reports are its own, stated in
`evidence-gathering`, `worker-protocol` and `spec-lifecycle`.

**Check it for a contradiction, and report rather than override.** If the project's file
states a different task-id or code-citation convention, surface it for the owner: the
harness's version governs the artefacts the harness checks — a task written the other way
fails `check-line-pins.sh` — but a silent override would leave two rules and no signal.
Never write the harness's conventions INTO that file; a copy drifts the moment the harness
changes, and it is the owner's document.

The harness reads it via
the agents that load it; it is not generated and not the harness's business.

Anything the harness *does* need from it should be in `harness.yaml` instead. If
you find yourself telling an agent a fact in prose that a check could read, that
fact wants to be config.
