# Skills

Thirteen skills in [`skills/`](../../skills/). A skill is **doctrine** — the rules an agent
must follow — loaded on demand rather than pasted into every prompt.

**Skills are the source of truth for procedure.** These docs link to them and do not
restate them, because a summary is a second source and it drifts.

## Process

<!-- GENERATED:skills — do not hand-edit; run harness/checks/check-docs.sh --write -->

| skill | carries |
|---|---|
| [`campaign-loop`](../../skills/campaign-loop/SKILL.md) | The shared epic-iteration procedure behind /campaign and /campaign-auto — find open epics, desig |
| [`design-fidelity`](../../skills/design-fidelity/SKILL.md) | How to implement or audit a screen against the Claude Design handover package — the handover sou |
| [`evidence-gathering`](../../skills/evidence-gathering/SKILL.md) | How to find out what is true about this repository without burning the budget doing it — the mea |
| [`framework-django`](../../skills/framework-django/SKILL.md) | Django doctrine for this repository: the services boundary, migration discipline, query performa |
| [`framework-nextjs`](../../skills/framework-nextjs/SKILL.md) | Next.js App Router doctrine for this repository: server versus client components, the data bound |
| [`harness-setup`](../../skills/harness-setup/SKILL.md) | Initialise, upgrade or repair this harness in a repository — write harness.yaml, pick the stack  |
| [`spec-lifecycle`](../../skills/spec-lifecycle/SKILL.md) | How a requirement becomes a spec and then code — what the staging directory holds, the two fold- |
| [`stack-node-npm`](../../skills/stack-node-npm/SKILL.md) | Running things in an npm-managed Node repository: scoped tests, the shared dependency directory, |
| [`stack-python-uv`](../../skills/stack-python-uv/SKILL.md) | Running things in a uv-managed Python repository: scoped test invocation, per-worker database is |
| [`test-doctrine`](../../skills/test-doctrine/SKILL.md) | The single testing standard for this repository — what "tested" means, what a decorative test lo |
| [`verification-gate`](../../skills/verification-gate/SKILL.md) | The rules the verification lenses share — what each lens is allowed to see and why that asymmetr |
| [`work-decomposition`](../../skills/work-decomposition/SKILL.md) | How to slice a goal or epic into a reviewable task DAG that a parallel wave can actually execute |
| [`worker-protocol`](../../skills/worker-protocol/SKILL.md) | The contract every swarm worker runs under — the isolated worktree and its per-worker database,  |

<!-- /GENERATED:skills -->

Technology skills are loaded only when the lane uses them, which is what keeps the
cost of supporting many technologies at zero for the ones not in use.

## How a skill reaches a dispatched agent

An agent *declares* its skills in frontmatter; what delivers them depends on the path. The
CLI preloads them only when it spawns the agent through the Agent tool, which the harness
never does. Through `dispatch.sh` the harness appends them to the prompt under
`dispatch.preload_declared` (on by default: the arm that carried the doctrine did what it
says — mutation testing 4× as often — at ~+60% per worker run in the lab), and with it off
the agent loads them on demand through the Skill tool, which measured at 26% for
`test-doctrine` and never for `worker-protocol`. `check-skills.sh` prints each agent's bill: the size
of that append, paid once into cache per dispatch and read back every turn.

The catalog a dispatched agent can load from is the plugin's skills plus the project's own
`.claude/skills/*` (`dispatch.lean_catalog`); the CLI's bundled skills and the plugin's
commands are not in it. See [cost](../concepts/cost.md).

## Setup

| skill | for |
|---|---|
| [`harness-setup`](../../skills/harness-setup/SKILL.md) | initialising or repairing the harness in a repository |

## The mirrored block

Two skills — `evidence-gathering`, `spec-lifecycle` — each carry a byte-identical
**harness conventions** block, so every agent that declares either gets the rules; the
check also fails any agent whose declarations include neither. The dispatcher injects a
two-line digest of the block into every prompt as well, for an agent whose skills have
not been delivered yet. `check-conventions-mirror.sh`
fails on a partial edit: change both or neither. (`worker-protocol` carried a third copy
until the writers took `evidence-gathering` and paid for it twice.)

That block lives in skills rather than a project file because of a lesson worth keeping.
Twenty-six citations of a consuming project's `CLAUDE.md` were audited and twenty turned
out to be **the harness citing its own rules** — doctrine parked in someone else's file and
then treated as external authority. It was never a dependency to remove; it was a fact in
the wrong place.

## Verifying

```bash
harness/checks/check-skills.sh   # declarations resolve, names match, no agent over-loads
```
