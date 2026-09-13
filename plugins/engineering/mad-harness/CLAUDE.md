# Working on the MAD harness

Guidance for developing **this repository**. If you are looking for what the harness *does*,
start at [docs/](docs/README.md).

## This file is not a doctrine store

A previous version of this project kept the harness's own rules in a consuming project's
`CLAUDE.md` and then cited them as external authority. An audit found **20 of 26 citations
were the harness citing itself**. That doctrine was repatriated into skills, where agents
actually load it.

So: **rules for how agents behave go in `skills/`. Rules for how this repo is built go
here.** If you find yourself writing "an agent must…" in this file, it belongs in a skill.

## Commands

```bash
make check        # lint, suite, project, skills, models, prose, commands — seconds
make conformance  # the tracker contract against every backend's real binary — ~1 min
make fmt          # format
```

`make check` must be green before any commit. `conformance` drives real binaries, so run it
whenever you touch `harness/tracker/`.

## Layout

| path | is |
|---|---|
| `commands/`, `agents/`, `skills/` | the prompt surface the plugin installs |
| `harness/` | the code: routing, dispatch, tracker, verification, checks |
| `harness/wavelab/` | the live differential lab — two repos, two backends, real waves |
| `templates/` | what a consuming project copies |
| `docs/` | this documentation |

## How work here is verified

Three levels, and the third catches what the first two cannot:

1. **`make check`** — fast, mechanical, must always pass
2. **`make conformance`** — both tracker backends against real binaries, never mocks
3. **`harness/wavelab/`** — real agents, real waves, a differential between backends

Several changes have passed 1 and 2 and broken a live wave. If you change dispatch,
permissions, or the tracker, run the lab:

```bash
harness/wavelab/reset.sh
harness/wavelab/dispatch-wave.sh beads && harness/wavelab/merge-wave.sh beads
harness/wavelab/compare.sh
```

## Generated artefacts

Two parts of `docs/` are generated and fail `make check` on drift. Do not hand-edit either.

| artefact | source | regenerate |
|---|---|---|
| tables between `GENERATED:` markers, `docs/llms.txt` | agent frontmatter, `skills/`, `commands/`, `checks/` | `harness/checks/check-docs.sh --write` |
| `docs/assets/*.svg` | `docs/assets/src/*.d2` | same command — needs `d2` installed |

`d2` is a contributor dependency, not a runtime one (`brew install d2`). Without it the
check reports that it cannot verify diagram drift rather than passing silently.

## Conventions that are enforced

These are checked, not merely asked for. `make check` will tell you.

- **Cite code by symbol, not line number** — `file.py::function`. A symbol survives edits
  above it.
- **No agent names a technology.** That reaches an agent through its lane's card.
- **Every mechanical guard carries a companion test proving it can fail.**
- **Two documents must not state the same fact.** Link, do not restate.

## Changing the plugin

Dispatched agents load the plugin **by path** (`--plugin-dir`), so code changes take effect
immediately. The *installed* plugin is separate and only matters for the interactive
commands you type — bump `.claude-plugin/plugin.json` and re-run
`claude plugin update mad-harness@aim` when you need those refreshed. The version
comparison is on the string, so an unbumped change will not install.

## Writing commit messages

Say what was wrong and how it was established. Measurement beats assertion: this corpus is
read by agents, and a commit that records *why* a rule exists is what stops the next person
deleting it.

## Adding to the harness

See [docs/guides/contributing.md](docs/guides/contributing.md) — every extension point, its
schema, and the check that verifies it.
