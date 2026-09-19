# Stack and framework modules

Two module axes, deliberately independent. A **stack** is how to *run* things — restore
dependencies, execute tests, isolate a worker. A **framework** is how to *write* things —
the services boundary, migration discipline, server versus client components. A Django
project on uv uses one of each; neither module knows the other exists, which is why one
toolchain serves many frameworks without an N×M matrix.

Shipped modules: [`harness/stacks/`](../../harness/stacks/),
[`harness/frameworks/`](../../harness/frameworks/). Schema reference:
[`_template.yaml`](../../harness/stacks/_template.yaml).

## Stack schema

```yaml
name: python-uv                    # MUST equal the filename minus .yaml
description: Python with uv for dependency management and pytest for tests.

detect_any:                        # TOOLCHAIN markers — a lockfile. Any one suffices
  - uv.lock
detect_language:                   # LANGUAGE markers — a manifest. Ambiguous alone
  - pyproject.toml

root: "."                          # the module NEVER names a location; the project does
dependency_dir: .venv              # relative to root

bootstrap:
  strategy: install                # install | symlink
  command: uv sync
  cwd: <subdir>                    # optional, relative to root
  env_notes: {DB_NAME: "why it matters"}   # printed into .swarm-env as a comment

env:                               # per-worker isolation; {slug} and {worker} substituted
  DB_NAME: "{slug}_w{worker}"

cache_env:                         # cache path INSIDE the sandbox boundary, repo-relative
  UV_CACHE_DIR: .harness/cache/uv

network:                           # the package index — reachable only from an orchestrator's sandbox
  - pypi.org
  - files.pythonhosted.org

commands:
  test: uv run pytest
  test_scoped: uv run pytest {path}
  test_first_run: uv run pytest --create-db
  lint: uv run ruff check .
  typecheck: uv run mypy .
  verify: uv run pytest --collect-only -q   # the cheap proof that `test` is still right

banned_forms:
  - pattern: '^\s*DB_NAME=\S+\s+\S'
    why: >-
      The string starts with DB_NAME=, not uv, so no prefix rule can match it...

card: |
  Python (uv):
  - Run tests through the harness runner...
```

| field | type | required | read by |
|---|---|---|---|
| `name` | `str` | yes | module resolution; must equal the filename |
| `description` | `str` | yes | `harness-setup`, docs generation |
| `detect_any` | `list[str]` | yes | toolchain detection |
| `detect_language` | `list[str]` | no | ambiguity reporting |
| `root` | `str` | no — ships `"."` | every path the stack resolves |
| `dependency_dir` | `str` | yes | worktree bootstrap |
| `bootstrap` | `dict` | yes | `swarm-worktree-init.sh` |
| `env` | `dict[str,str]` | no | `.swarm-env`, per worker |
| `cache_env` | `dict[str,str]` | no | dispatch env **and** the sandbox write policy |
| `network` | `list[str]` | no | the sandbox egress policy of an **orchestrator** dispatch — a worker's worktree init runs inside it there; a worker itself gets no egress |
| `commands` | `dict[str,str]` | yes | `verify/run.sh`, the wave gate, `check-stack-commands.sh` |
| `banned_forms` | `list[dict]` | no | worker guidance |
| `card` | `str` | yes | injected into every dispatch in this lane |

## Command keys

| key | used for | `{path}` |
|---|---|---|
| `test` | the whole-repo wave gate | no |
| `test_scoped` | a worker's scoped run | **yes** |
| `test_first_run` | first run in a fresh worktree (e.g. `--create-db`) | no |
| `lint` | the lint gate | no |
| `typecheck` | the typecheck gate, where declared | no |
| `verify` | the cheap proof that `test` still resolves — collection without execution | no |

`verify` is what `check-stack-commands.sh` probes at pre-flight. It loads the runner, its
config and every test module **without running anything** — measured at ~3.4s against a
suite that takes 130–187s — which is what makes a pre-flight probe affordable.

A command the project does not declare is reported `--`, never silently skipped.

## detect_any vs detect_language

The distinction is load-bearing and the reason is worth stating once.

| field | takes | proves |
|---|---|---|
| `detect_any` | a **lockfile** — `uv.lock`, `package-lock.json` | this toolchain |
| `detect_language` | a **manifest** — `pyproject.toml`, `package.json` | the language only |

A manifest is shared by every tool in an ecosystem. Treating one as proof lets a Poetry
project validate as uv, then fail inside a worker's worktree mid-wave — where the
escalation policy reads it as the worker's fault rather than the config's.

Matching a language marker and **no** toolchain marker is reported **ambiguous**: a third
answer, and the honest one.

## bootstrap.strategy

| strategy | when | cost of getting it wrong |
|---|---|---|
| `install` | restoring is cheap — the manager hardlinks or caches | minutes per worker, every wave |
| `symlink` | restoring is expensive **and** concurrent readers are safe | corrupted shared state if readers are not safe |

## env — per-worker isolation

`{slug}` and `{worker}` are substituted. **Every stack that needs isolation must declare
it.** A worker without its own database or build directory silently shares one with its
siblings — and the suite still passes, which is why nobody notices until an unrelated flake
days later.

## cache_env — one declaration, two effects

```yaml
cache_env:
  UV_CACHE_DIR: .harness/cache/uv
```

1. exported into the dispatch environment, resolved against the **primary checkout** so
   every worker shares one cache
2. added to the sandbox's `filesystem.allowWrite`

They must agree, and a test pins that they do: a cache pointed somewhere the sandbox
forbids is the failure this field exists to prevent. Granting the toolchain's *default*
home-directory cache instead was measured and does not work — `uv` fails `EPERM` on a file
**inside** the granted directory.

## card — a token budget

The card is injected into every dispatch in the lane, so every line is paid for on every
dispatch. The bar from `_template.yaml`: state only the rules that are wrong often enough
to be worth the tokens.

The card is also the **only** route by which a technology name reaches an agent. Agents
name no technology themselves — `test_no_agent_names_a_technology` enforces that — so an
agent serves a Django project and a Next.js project unchanged.

```bash
harness/checks/stack-card.sh <lane>    # print what a dispatch in this lane will carry
```

## Overrides merge

```yaml
# harness.yaml
stacks:
  - name: python-uv
    root: services/api
    commands:
      test_scoped: uv run pytest {path} --no-header
```

Naming one command leaves the module's others intact. Replacement semantics would mean
fixing one rotted command silently dropped the five beside it.

## Adding a module

See [contributing](../guides/contributing.md).
