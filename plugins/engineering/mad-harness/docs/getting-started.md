# Getting started

Install the plugin, describe your repository in `harness.yaml`, create a task graph, run a
wave. The first three are one-time; the fourth is the loop you stay in.

Check [requirements](requirements.md) first — dispatch refuses to run without an OS sandbox,
and that is a hard failure rather than a degraded mode.

## 1. Install

```bash
claude plugin marketplace add matt-chalmers/aim   # or a local clone path
claude plugin install mad-harness@aim
claude plugin list | grep mad-harness
```

The installed copy serves the interactive commands you type. Dispatched agents load the
plugin **by path**, so if you are developing the harness itself your changes take effect
without reinstalling — see [CLAUDE.md](../CLAUDE.md).

After a `claude plugin update`, run `/harness-setup` once in the repository. It reads the
[upgrade notes](upgrading.md) for every version since your `harness.yaml` was last
reviewed, applies them, and re-stamps the file; until then the `/campaign` and `/swarm`
pre-flights stop with `UPGRADE`.

Run everything from inside your repository. The plugin lives in `~/.claude/plugins/cache/`,
so each script finds the project from the directory you call it in; from anywhere else, set
`MAD_HARNESS_REPO=/path/to/repo`. Every check prints `project: <name>` first — if that is
not your project, it resolved the wrong one
([troubleshooting](guides/troubleshooting.md#the-backlog-reads-as-empty-or-a-check-answers-about-the-wrong-project)).

## 2. Describe the repository

```
/harness-setup
```

`harness.yaml` is the only file the harness needs from you, and it is the seam that makes
everything else portable. The skill reads what it can — lockfiles, layout, existing test
commands — and asks about what it cannot infer.

Expect it to push back in two places, because neither can be guessed safely:

- **The security surface.** Which paths are sensitive decides when L4 fires.
- **The test standard.** What "tested" means here is what L2 judges against.

Minimum viable config:

```yaml
name: Acme Widgets
slug: acme                      # lowercase, no hyphens — names per-worker resources

stacks:
  - name: python-uv
    root: services/api          # the module ships root: "."; you say where it lives

paths:
  docs: docs
  proposed: docs/proposed       # staging folders for in-flight epics

testing:
  command_key: test
```

Verify:

```bash
harness/checks/check-project-config.sh    # schema, paths, archive invariant
harness/checks/check-stack-commands.sh    # probes each command; repairs what rotted
```

The second matters more than it looks. It runs your declared commands and, when one has
rotted, derives a replacement from what the repo already declares and proves it by running
it. Catching that at pre-flight is the difference between a config error and a worker
failure mid-wave, where the escalation policy reads it as the worker's fault.

Full block reference: [harness.yaml](reference/harness-yaml.md).

## 3. Create a task graph

The harness schedules from a DAG, so it needs one before it can do anything.

```
/requirements     # specify an epic with the owner, interactively
/plan-swarm       # decompose into a reviewable DAG; you approve before records are created
```

```bash
harness/tracker/tk.sh ready            # what would be dispatched now
harness/tracker/tk.sh validate <epic>  # cycles, orphans, levelling, max parallelism
```

`validate` is worth reading before the first wave. It reports the wave structure the planner
produced, and `max_parallelism` tells you whether the decomposition is actually parallel or
a chain wearing a DAG's clothes.

## 4. Run a wave

```
/swarm
```

What happens, in order:

| step | effect |
|---|---|
| pre-flight | clean-tree check; tracker `autosync off` |
| dispatch | one worker per ready task, each in its own worktree |
| claim | `O_EXCL` per task — a double-dispatch loses rather than corrupting |
| implement | worker edits, runs the scoped suite, commits once |
| merge | serialised, one branch at a time |
| gate | whole-repo, **once**, on the merged tree |
| sync | orchestrator writes the tracked export |
| push | one push per wave |

```bash
git log --oneline --graph -10          # one merge per task
harness/tracker/tk.sh list             # tasks closed with reasons
```

If a dispatch reports not-ok, read **stderr**, not stdout — denials are printed there. See
[troubleshooting](guides/troubleshooting.md).

## 5. Run the queue

```
/campaign         # stops at each epic boundary
/campaign-auto    # unattended
```

`/campaign-auto` merges to your default branch without a human in the loop. That is the mode
the [permission model](concepts/permissions.md) is designed around, and the reason the
sandbox is mandatory rather than recommended.

## Try it without touching your code

`harness/wavelab/` builds two throwaway repositories, seeds the same epic in each, and runs
real waves against both backends. It is the harness's own differential test and the fastest
way to watch a wave land.

```bash
harness/wavelab/reset.sh
harness/wavelab/dispatch-wave.sh mdfiles
harness/wavelab/merge-wave.sh mdfiles
harness/wavelab/compare.sh
```

## Next

| | |
|---|---|
| [Architecture](concepts/architecture.md) | the two halves, and where a new rule belongs |
| [The loop](concepts/the-loop.md) | what those commands are doing |
| [Workers](reference/workers.md) | isolation and the shared-state exceptions |
| [Customising](guides/customising.md) | when the setup guessed your layout wrong |
