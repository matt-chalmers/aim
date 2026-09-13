# `harness/` — the code half

Shell and Python executed *by a machine*: deterministic, testable, exits non-zero. The
other half — `agents/`, `commands/`, `skills/` — is markdown read *by a model*.

**The documentation lives in [`docs/`](../docs/README.md).** This file is orientation for
someone opening this directory; it does not restate what those pages own.

| directory | owns | documented in |
|---|---|---|
| `models/` | routing, the dispatch boundary, permissions, config, telemetry | [agents and tiers](../docs/concepts/agents-and-tiers.md), [permissions](../docs/concepts/permissions.md) |
| `tracker/` | four ports, two backends, graph, locks, events, render, archive | [tracker ports](../docs/reference/tracker-ports.md) |
| `verify/` | briefs, scoped runs, mutation, fidelity, batched reads | [verification](../docs/concepts/verification.md), [scripts](../docs/reference/scripts.md) |
| `swarm/` | worktree lifecycle | [scripts](../docs/reference/scripts.md) |
| `campaign/` | campaign telemetry | [scripts](../docs/reference/scripts.md) |
| `checks/` | the mechanical gates | [checks](../docs/reference/checks.md) |
| `stacks/` `frameworks/` | the two module axes | [stacks](../docs/reference/stacks.md) |
| `tests/` | this harness's own suite | [contributing](../docs/guides/contributing.md) |
| `wavelab/` | the live differential lab — two repos, two backends, real waves | [contributing](../docs/guides/contributing.md) |

## Running it

```bash
make check                          # everything, from the repo root
make harness-test                   # this suite alone
make conformance                    # the tracker contract, against real binaries
harness/checks/check-line-pins.sh   # any check, individually
```

## The rule for adding anything here

**Does it ship, or does it run the agents that build what ships?** Application tooling — a
script that brings up your real stack, or runs your end-to-end suite — would exist with no
agents involved, so it belongs with your application rather than here.

See [architecture](../docs/concepts/architecture.md) for the fuller version of that split,
and [contributing](../docs/guides/contributing.md) for each extension point's schema.
