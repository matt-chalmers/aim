# `harness.yaml`

The one file that makes the harness fit your repository. Written by
[`/harness-setup`](../getting-started.md), verified by `check-project-config.sh`.

## Blocks

| block | declares | required |
|---|---|---|
| `name` / `slug` | display name; lowercase slug that names per-worker resources | yes |
| `harness` | `version`: the plugin version this config was reviewed against — the pre-flights stop when the plugin is newer; see [Upgrading](../upgrading.md) | no, but unstamped is treated as behind |
| `stacks` | which toolchains, and where each lives | yes |
| `frameworks` | how to write good code here — independent of the toolchain | no |
| `tracker` | which backend, and where its records live | no (defaults to `beads`) |
| `beads` | the id prefix the task-hygiene checks build their patterns from | yes, on the beads backend |
| `swarm` | wave sizing and worker resources | no |
| `ports` | every TCP port your servers bind, by name — the pre-flight probes them | no |
| `paths` | docs, staging, archive — **omit any your project lacks** | yes |
| `domain` | your domain vocabulary — prompts are guarded against naming it | no |
| `lanes` | concurrency per lane, measured on your hardware | no |
| `areas` | groups changed paths, and decides which lens a change must face | no |
| `security` | the security surface — worth a real conversation, not a guess | no |
| `testing` | where tests live and what the project holds itself to | yes |
| `layout` | roles → paths (the API surface, the services boundary…) | no |
| `lenses` | extra lenses to dispatch — the extension point | no |
| `signals` | health baselines, which are **measurements** of this repo | no |
| `permissions` | grants an operator approved — see below | no |

Full annotated example: [`templates/harness.yaml.example`](../../templates/harness.yaml.example).

## `permissions` — normative, and agent-unwritable

```yaml
permissions:
  allow:
    - Bash(curl https://pypi.org/*)   # answered PROJ-4f2a, narrowed from Bash(curl:*)
```

The only way the allowlist grows. An agent may **request** a grant by filing a
`Permission:` record; only a person may write one here. `check_commands._NORMATIVE` refuses
every agent-initiated write to this key, so an agent that can ask is structurally unable to
approve. A grant here can never defeat a deny rule.

Grant the **narrowest** rule that unblocks the work. A list that only ever widens has
stopped being a control.

## The three rules that decide where a fact goes

Learned by getting each wrong, and each pinned by a test.

**1. A module names no location.** A stack ships `root: "."`; the project says where the
toolchain actually lives. Before this, one directory name appeared in four fields of one
module, kept in agreement by hand.

**2. Detection names the TOOLCHAIN, not the language.** `detect_any` takes the lockfile;
`detect_language` takes the manifest. A manifest is shared by every tool in an ecosystem,
so treating one as proof lets another tool's project validate clean and fail inside a
worker's worktree mid-wave. Matching a language marker and no toolchain marker is reported
**ambiguous** — a third answer, and the honest one.

**3. A command is a PROJECT fact wearing a toolchain's clothes.** `uv run` is a property of
uv; `pytest` is a choice you made. The module ships a default, `harness.yaml` overrides per
stack — and **overrides merge rather than replace**, so fixing one rotted command does not
silently drop the five beside it.

## Verifying it

```bash
harness/checks/check-project-config.sh   # schema, paths, and the archive invariant
harness/checks/check-stack-commands.sh   # probes each declared command, repairs what rotted
```
