# aim

Claude Code plugins, grouped into collections.

## Install

```bash
claude plugin marketplace add matt-chalmers/aim
claude plugin install <plugin>@aim
```

## Plugins

| collection | plugin | does |
|---|---|---|
| `engineering` | [mad-harness](plugins/engineering/mad-harness/) | a multi-agent development harness — model tiering and dispatch, parallel worker worktrees, decorrelated verification lenses, and the doctrine those agents run on |

## Layout

```
plugins/<collection>/<plugin>/
  .claude-plugin/plugin.json     the plugin manifest
  ...                            whatever that plugin ships
```

Every plugin lives under `plugins/`, one level inside a collection. The prefix is
deliberate: it keeps collections from competing with this repository's own `docs/`,
`scripts/` and `.github/` at the root, which stops being legible as soon as there is more
than one collection.

`.claude-plugin/marketplace.json` at the root is the index. A plugin is only installable
once it is listed there, with a `source` naming its directory.

## Adding a plugin

1. Create `plugins/<collection>/<name>/` with a `.claude-plugin/plugin.json` whose `name`
   matches the directory.
2. Add an entry to `.claude-plugin/marketplace.json` with `source` set to
   `./plugins/<collection>/<name>`.
3. Run `scripts/validate-marketplace.sh`.

A new collection is just a new directory under `plugins/` — nothing registers it but the
`source` paths that point into it.

## Validation

```bash
scripts/validate-marketplace.sh
```

Checks what no individual plugin's own tests can see: that the manifest and the directory
tree agree. Every entry's `source` must exist, carry a `plugin.json`, and declare a
matching name — and every plugin directory must be registered. CI runs it on every push,
then runs each plugin's own checks.
