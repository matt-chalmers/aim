# Requirements

Four things on the machine, and one thing about your repository. Each is listed with what
breaks without it, because a prerequisite without a consequence is one people skip and then
debug.

| dependency | needed for | without it |
|---|---|---|
| **git** ≥ 2.20 | `git worktree` — the isolation model | workers cannot be isolated; no parallel wave |
| **[uv](https://docs.astral.sh/uv/)** | every entry point; resolves the harness's own dependencies | nothing runs |
| **a sandbox** | OS containment for dispatched agents | `dispatch()` raises `SandboxUnavailable` and refuses |
| **[Claude Code CLI](https://code.claude.com/docs/en/overview)** | the interactive commands you type | `/swarm` and friends are unavailable; dispatch still works |

```bash
git --version && uv --version && claude --version
```

## And one thing about your repository: a stack module

The harness has to know how to restore dependencies in a fresh worktree and how to run your
suite. That is a *stack module*, and only a few ship — see
[what ships](reference/stacks.md#what-ships).

If yours is not among them, nothing is blocked and no code changes: you write one YAML file
in your own repository, and `/harness-setup` walks you through it as part of setup. Budget
for it before your first wave rather than during one.

## Install

```bash
# uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Claude Code
npm install -g @anthropic-ai/claude-code

# sandbox — Linux/WSL2 only; macOS needs nothing
sudo apt install bubblewrap        # Debian/Ubuntu
sudo dnf install bubblewrap        # Fedora
```

You install nothing else. `uv` resolves the harness's dependency set from
`harness/pyproject.toml` on first run.

## The sandbox is mandatory

`require_sandbox()` runs before anything else in `dispatch()`.

| platform | mechanism | status |
|---|---|---|
| macOS | Seatbelt | built in — nothing to install |
| Linux / WSL2 | bubblewrap (+ optional seccomp filter) | install `bubblewrap` |
| Windows (native) | — | **unsupported** — run inside WSL2 |

```
SandboxUnavailable: this harness dispatches only on machines where the sandbox can be
enforced, and it cannot be here: bubblewrap is not installed. Refusing rather than falling
back to permission rules alone, which is a posture this project does not support.
```

The refusal is the feature. Workers run unattended and merge to the default branch, so
silent degradation to permission rules alone would put an uncontained agent on your
repository with nothing reporting it.

## Choose a tracker backend

| backend | external dependency | records are | choose when |
|---|---|---|---|
| `mdfiles` | none | markdown, one file per open record | you want task state to diff and review like code |
| `beads` | [`bd`](https://github.com/steveyegge/tasks) on PATH | a database with a JSONL export | you already use it, or want its query surface |

Both satisfy the same conformance contract, run against each backend's real binary.
Declared in one block:

```yaml
tracker:
  backend: mdfiles
```

## Credentials

Anthropic access comes from Claude Code's own auth — a subscription or an API key — and the
harness adds nothing. It only needs a file of its own if you route a tier at another
provider:

```bash
cp harness/.env.example harness/.env && chmod 600 harness/.env
```

That file is gitignored and the tracked template is `harness/.env.example`. A credential is
referenced from `harness.yaml` as `${VAR}` and never written into it, into a task, or into
telemetry — `harness.yaml` is committed. See [providers](concepts/providers.md).

**Say which pocket pays.** If you are on a plan rather than an API key, declare it:
`providers: {anthropic: {billing: subscription}}`. Nothing can detect this, and it changes
how every cost report totals.

## Optional

| tool | for | without it |
|---|---|---|
| **[d2](https://d2lang.com/tour/install)** | regenerating documentation diagrams | `check-docs.sh` reports it cannot verify diagram drift, and passes |
| **node** | the fidelity tooling (`verify/fidelity-*.mjs`) | design-fidelity checks are unavailable |

## Verify the installation

```bash
harness/checks/check-project-config.sh    # config schema, paths, archive invariant
harness/checks/check-stack-commands.sh    # probes declared commands, repairs what rotted
harness/models/dispatch.sh verifier --prompt-file /dev/null --dry-run
```

The dry run resolves tier, model, budget, permission mode, grants and sandbox settings
without spending anything. If it prints a plan, the installation is sound.
