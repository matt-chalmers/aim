# Stage fixtures — one per gate, each shaped to trip it

The wave test proves the plumbing: dispatch, worktrees, claims, commits. It says nothing
about the **gates**, which is where a campaign's value actually sits — an epic that should
be parked, a plan that should be serialised, a test that should be failed.

Each fixture here is a repository state plus a prompt, dispatched at the agent that owns
that gate, with an outcome that can be checked. **A gate that never fires is
indistinguishable from a gate that always passes**, and only a fixture built to fail it
tells the two apart.

| stage | agent | the fixture is | passing means |
|---|---|---|---|
| `adequacy-absent` | `analyst-survey` | an epic with a title and nothing else | verdict `ABSENT` — and NO invented scope |
| `adequacy-ok` | `analyst-survey` | the same epic, fully specified | verdict `ADEQUATE` |
| `lens-correctness` | `verifier` | a change that misses one stated criterion | `FAIL`, naming the missed criterion |
| `lens-tests` | `verifier-tests` | a test that passes with the feature deleted | `FAIL`, naming it decorative |
| `lens-spec` | `verifier-spec` | code whose behaviour contradicts its doc | `FAIL`, naming the doc |
| `plan-contention` | `planner` | two tasks that must edit one file | the two are serialised, not put in one wave |

**The negative cases matter more than the positive ones.** `adequacy-ok` exists so that a
survey which returns `ABSENT` for everything cannot pass this suite by being pessimistic —
the same reason a mutation-testing run reports a control.
