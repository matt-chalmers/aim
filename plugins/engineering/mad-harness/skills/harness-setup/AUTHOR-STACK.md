# Authoring a stack module

The breakout from the `stacks` block of `harness-setup`, for a toolchain in play that no module
covers. A new module is one YAML file under `.harness/stacks/<name>.yaml` in the consuming
repository — tracked, project-local, needing no change to the harness. Most of it is derived:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/setup/derive.sh stack-breakout --root <root>
```

## The shape

```yaml
name: <toolchain>              # MUST equal the filename
root: "."                      # the module names NO location; the project's harness.yaml does
detect_any: [<lockfile>]       # the marker for THIS TOOLCHAIN — any one is enough
detect_language: [<manifest>]  # the looser marker; matching only this is AMBIGUOUS
dependency_dir: <gitignored dir a fresh worktree lacks>   # relative to root
bootstrap:
  strategy: install | symlink
  command: <restore command>   # install only
env:
  SOME_VAR: "{slug}_w{worker}" # per-worker isolation
banned_forms: []               # owed whenever env is non-empty
commands:
  test: <whole suite>
  test_scoped: <suite for one path> {path}
  verify: <the cheapest command that proves the runner still works>
card: |
  <prohibitions a worker in this toolchain must not break>
```

## The steps

| step | how |
|---|---|
| `name` | the owner's; the loader enforces filename == `name:`, so the mistake is caught |
| `detect_any` / `detect_language` | **a pick-list, never typed**: the root's real files. The owner picks the one that marks *this tool* — the lockfile — versus the language's manifest |
| `dependency_dir` | proposed: the largest gitignored directory at the root, shown with its size |
| `bootstrap.command` | proposed from CI, Makefile and package-script lines that install, sync or restore |
| `bootstrap.strategy` | **measured**: `derive.sh stack-breakout --root <root> --time-bootstrap "<command>"` runs it in a scratch worktree and recommends from the number |
| `env` | owed — see below. Candidate names come from `.env.example` |
| `banned_forms` | **owed whenever `env` is non-empty**: the command forms that would bypass the isolation |
| `commands` | proposed and proved by running; `verify` must be cheap — reject one over ~15s and say the number (the reference is ~3s) |
| `cache_env` / `network` | empty by default. They come back when `uv`-style "cannot initialise cache … not permitted" errors appear inside a worker's sandbox, or a restore cannot reach its index |
| `card` | prohibitions only, within the card budget (`check-skills.sh` enforces it) |

**Name the toolchain's own marker, not the language's.** `pyproject.toml` is shared by uv,
Poetry, PDM, Hatch and plain pip; `package.json` by npm, pnpm, yarn and bun. A marker that
identifies only the language lets another tool's project pass config validation and fail later
inside a worker's worktree, mid-wave — where the escalation policy reads it as the worker's fault
rather than the config's.

**Choose `strategy` on evidence, not habit.** `install` when restoring is cheap (a package
manager that hardlinks from a global cache); `symlink` when it is expensive and concurrent
readers are safe. Getting this backwards costs minutes per worker, every wave.

**`env` is the one that fails silently.** Present it as its consequence, not its schema: a worker
without its own database or build directory shares its siblings', and the suite still passes —
which is why nobody notices.

**Give it a `verify`.** A module with none is reported UNPROVEN rather than assumed good — a real
answer, and a fine place to start.

## The proof — write, declare, lane, then three checks

Write the file with Write, declare it in the `stacks` block, give it a lane in the `lanes` block,
then:

| proof | a failure means |
|---|---|
| `${CLAUDE_PLUGIN_ROOT}/harness/checks/check-project-config.sh` | the module does not load, or its markers are absent under the declared root |
| `${CLAUDE_PLUGIN_ROOT}/harness/checks/check-stack-commands.sh` | a declared command does not run here |
| `${CLAUDE_PLUGIN_ROOT}/harness/swarm/probe-worktree.sh <lane>` | the bootstrap restores nothing, `.swarm-env` does not source in a real shell, or two workers share a resource |

When the probe *reports* that no per-worker variable is declared, that is the decision point for
`env`, not a pass.
