# Contributing

Every extension point has a schema, a check that verifies it, and a test proving the check
can fail. This page is the procedure for each.

## Before and after any change

```bash
make check        # lint · suite · project · skills · models · prose · commands · docs
make conformance  # the tracker contract against every backend's real binary (~1 min)
```

`make check` must be green before a commit. Run `conformance` whenever you touch
`harness/tracker/` — it drives real binaries, which is the point.

| change touches | also run |
|---|---|
| `harness/tracker/` | `make conformance` |
| dispatch, permissions, the tracker | `harness/wavelab/` — see [end to end](#testing-end-to-end) |
| `docs/assets/src/*.d2` | `harness/checks/check-docs.sh --write` |
| an agent's `model_tier` | `harness/checks/check-model-config.sh` |
| a cost lever's default | `harness/wavelab/ab.sh <lever>` — a default moves only on numbers whose spreads separate |
| `harness/orchestrator-card.md` | `harness/checks/check-orchestrator-card.sh --write` — every command carries it |

---

## Add an agent

**1.** Create `agents/<name>.md`:

```markdown
---
description: <when the orchestrator should reach for this>
tools: Read, Grep, Glob, Bash, Skill          # Edit|Write here makes it a WRITER
model_tier: strong                            # worker | strong | strategic
skills: [evidence-gathering]
isolation: worktree                           # writers only
---

<the prompt body>
```

**2.** Verify:

```bash
harness/checks/check-model-config.sh
harness/checks/check-skills.sh
```

**3.** Dry-run it — this resolves routing and permissions without spending anything:

```bash
harness/models/dispatch.sh <name> --prompt-file /tmp/x.txt --dry-run
```

| constraint | enforced by | why |
|---|---|---|
| names no technology | `test_no_agent_names_a_technology` | an agent naming a toolchain cannot serve a project using another; the failure is confident wrong advice, not an error |
| cites code by symbol, not line | `check-line-pins.sh` | a symbol survives edits above it |
| `tools` decides writer/reader | `permission_for()` | nothing keys off the agent's name — a name list goes stale the first time an agent changes shape |
| declared tier exists | `check-model-config.sh` | two readers of the tier must not drift |

---

## Add a skill

**1.** Create `skills/<name>/SKILL.md` with `name` and `description` frontmatter.
**2.** Declare it in the `skills:` list of every agent that needs it.
**3.** `harness/checks/check-skills.sh`

| the content is… | goes in |
|---|---|
| *what exists* — a schema, a contract, an inventory | [`docs/`](../README.md) |
| *what an agent must do* — procedure, judgement, a rule | a skill |

**Touching the mirrored conventions block means editing both carriers** —
`evidence-gathering` and `spec-lifecycle`. `check-conventions-mirror.sh` fails on a partial
edit, and fails any agent whose preloads include neither.

**Skills cost tokens on every dispatch that preloads them.** A test asserts that adding a
module leaves every agent's preload bill unchanged; keep a skill to the rules that are
wrong often enough to be worth the budget. Note what "preload" means under dispatch:
the CLI does not deliver an agent's frontmatter `skills:` to a `--agent` session; the
harness appends them when `dispatch.preload_declared` is on, and otherwise the agent
loads them on demand. The bill `check-skills.sh` prints is the size of that append.

---

## Add a stack or framework module

**1.** Copy [`_template.yaml`](../../harness/stacks/_template.yaml).
**2.** Fill it against the [schema](../reference/stacks.md), minding four fields:

| field | get wrong and |
|---|---|
| `detect_any` vs `detect_language` | another tool's project validates clean, then fails inside a worker's worktree mid-wave |
| `root` | ships `"."` — a module that names a location is wrong for everyone else |
| `cache_env` | a sandboxed worker fails every command with *Operation not permitted* |
| `bootstrap.strategy` | `install` vs `symlink` backwards costs minutes per worker, every wave |

**3.** Verify:

```bash
harness/checks/check-stack-commands.sh     # probes each command; repairs what rotted
harness/checks/stack-card.sh <lane>        # what a dispatch in this lane will carry
```

**4.** Confirm the card earns its tokens. It is injected into every dispatch in the lane.

---

## Add a command

**1.** Create `commands/<name>.md`:

```markdown
---
description: <one line — shown in the command list>
argument-hint: "[optional: a task id]"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/harness/*), Read, Glob, Grep
---
```

**2.** Invoke harness scripts as `${CLAUDE_PLUGIN_ROOT}/harness/…`. A command spelled with a
shell variable can never be permitted — rules match the text before the shell expands
anything.

**3.** `make check`.

`allowed-tools` governs the **interactive session**, not dispatched workers. Those are
governed by [permissions](../concepts/permissions.md), derived per agent.

---

## Add a check

**1.** Write `harness/checks/check-<thing>.sh` — no arguments, one line of output when
healthy, non-zero exit on failure. Keep the logic in an importable Python module so tests
exercise it directly rather than through a subprocess.

**2.** **Write the companion test proving it can fail.** Plant a violation, assert the check
returns non-zero, revert, assert it returns zero.

**3.** Add it to the `check` target in the `Makefile`.

| pitfall | how it bites |
|---|---|
| a corpus sweep matching its own documentation | the text explaining a banned pattern contains it — this has happened three times here |
| a check that cannot fail | passes forever; decoration, not a guard |
| a check that cries wolf | gets ignored, and then so does the real failure |

Scope sweeps to shipped code and to **invocations**, not prose.

---

## Add a cost lever

A lever is a switch that is off until the lab has sized it. Read `models/levers.py`'s
docstring first — it states the precedence and the one exception.

**1.** Name it in `levers._ENV` (`MAD_HARNESS_<LEVER>`) and `_DEFAULT`; add its `harness.yaml`
key to `Project.dispatch()` with validation.
**2.** Read it in exactly one place — `Resolved.sdk_options`, `build_env` or `with_context`.
**3.** Add its two arms to `wavelab/ab.sh` and its row to the lever table in
[cost](../concepts/cost.md).
**4.** Tests: the switch reaches the SDK option / env / prompt when on and is absent when
off; the yaml key is validated; `snapshot()` records it.
**5.** Run the series (`ab.sh <lever> --runs 5`), read `ab-report.sh`. The default moves only
if the spreads separate, in its own patch release, with the numbers in `upgrading.md`.

---

## Add a hook

The plugin installs hooks from `hooks/hooks.json`; a hook denies or informs, never grants.

**1.** Logic in `models/<name>.py` with a `decision(payload) -> dict | None` (or a `main()`
that reads the hook payload on stdin); a wrapper `swarm/<name>.sh` of the standard form.
**2.** Register it: `"command": "\"${CLAUDE_PLUGIN_ROOT}/harness/swarm/<name>.sh\""` with a
matcher and a timeout. Paths the hook *prints* must be absolute — hook output is injected
verbatim, and `${CLAUDE_PLUGIN_ROOT}` is substituted only in skill and command text.
**3.** A hook fires in dispatched workers too. Decide whether it should (the pinned-state
hook is silent when the payload names an agent).
**4.** Tests: the deny case, the pass case, a broken payload never breaking the tool call,
and the registration itself. Pipe-test the wrapper end to end.

---

## Add a telemetry metric

**1.** Extend the payload in `models/dispatch.py::Outcome.telemetry` — or, for something
read from the session transcript, `models/transcript.py::ResultVolume`.
**2.** Read it back in `models/report.py::summarise`, and in `models/ab_report.py::METRICS`
if a series should compare it.
**3.** Confirm `make models-cost` still renders, and document the field in
[cost](../concepts/cost.md).

**Never put a credential in it.** `Resolved.redacted()` is the only serialiser, and it
emits variable **names** — a provider token rendered into a record survives in the tracked
export. Recording failures must stay reported-and-swallowed: telemetry must never fail a
dispatch that already succeeded.

---

## Add a tracker backend

**1.** Implement `TaskStore` and `MemoryStore` from
[`port.py`](../../harness/tracker/port.py). **Not** `Coordination` or `Telemetry` — those
are one local implementation serving every backend, because coordination is a POSIX problem
and has nothing to do with where records are stored.

**2.** Register it in `tracker/__init__.py::task_store`.

**3.** `make conformance` — 54 tests against your real binary.

| contract | why it is easy to miss |
|---|---|
| `show()` resolves a **closed** record | otherwise "dependency satisfied" is indistinguishable from "dependency missing" |
| `close()` records the reason in `close_reason` | one backend accepted it and dropped it, silently |
| every record type round-trips | one backend validates its type vocabulary and rejects unknown values |
| `capabilities()` declares `owned_paths` | consumers otherwise hardcode `.beads/` |
| no signature takes or returns a path | two POSIX backends will otherwise encode that into the interface |
| `raw` is diagnostics only | branch on it and the abstraction is decorative |

---

## Diagrams

Sources are `docs/assets/src/*.d2`; the SVGs are generated and must not be hand-edited.

```bash
brew install d2                            # or https://d2lang.com/tour/install
harness/checks/check-docs.sh --write       # re-render every diagram
harness/checks/check-docs.sh               # fails on drift; part of make check
```

`--layout tala` is fixed in `models/check_docs.py`, chosen by rendering the same sources
through all three bundled engines: dagre laid the loop out as a 6.7:1 strip, which pushed
its side nodes to the edges with edges crossing back; TALA produced a 1:1 canvas with none.
It is deterministic across runs, which the drift check depends on.

If a diagram routes badly, reach for d2's own constraints — `near` to pin a node relative
to another, declaration order, or a container to group related nodes — before changing the
engine. Without d2 installed the check reports it cannot verify rather than passing
silently.

---

## Testing end to end

Unit tests and the conformance contract do not catch everything. Several changes have
passed both and broken a live wave.

```bash
harness/wavelab/reset.sh                    # two throwaway repos, same epic, both backends
harness/wavelab/dispatch-wave.sh beads
harness/wavelab/merge-wave.sh beads
harness/wavelab/lens-wave.sh beads          # re-judge landed tasks without a new wave
harness/wavelab/compare.sh                  # IDENTICAL, or the difference
```

`compare.sh` compares outcomes and deliberately ignores ids, timestamps, backend storage
paths and permission records — all of which differ by construction or by agent behaviour. A
differential that can never report IDENTICAL teaches its reader to ignore it.

---

## Commit messages

State what was wrong and how it was established. Measurement beats assertion: this corpus
is read by agents, and a commit recording *why* a rule exists is what stops the next person
deleting it.
