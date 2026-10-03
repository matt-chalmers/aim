---
name: harness-setup
description: Initialise, upgrade, revisit or repair this harness in a repository — walk harness.yaml block by block with the owner, deriving what the repository already says, detecting toolchains and frameworks in play (including ones no module covers yet), and proving the result end to end. Invoke it when harness.yaml is missing, when the config check reports UPGRADE or a toolchain present but not declared, when a check reports the config disagrees with the repo, or to add support for a new toolchain or framework.
argument-hint: "[revisit | repair <block>]"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/harness/*), Bash(git:*), Read, Glob, Grep, Edit, Write, AskUserQuestion
---

# Harness setup

The harness is portable; `harness.yaml` and the stack and framework modules are what make it
about *this* repository. This walks the owner through writing them — and back through them
later, because a repository gains a toolchain and an upgrade changes what the config must
say.

**Never generate a plausible config and present it as done.** A wrong `slug` silently names
every worker database; a missing `security` invariant is not a warning but a lens that never
fires. Everything below exists so the owner confirms what the repository says and answers only
what it cannot.

## The protocol — every block, the same seven beats

```
DERIVE     derive.sh --block <id>: values, evidence, candidates, owed. Nothing shown yet.
VALIDATE   check-project-config.sh over the draft; a derived value it rejects moves to owed.
WRITE      write.sh <id> < values  — the derived values, before anyone is asked. No ledger.
SHOW       at most two lines, then what is OWED, only if anything is.
ASK        present → confirm / change / extend, then collect what is owed.
RE-WRITE   write.sh <id> --state confirmed|declined [--because "…"] < the answer
FOLLOW UP  if the answer revealed a question nobody asked, ask it now.
```

```bash
${CLAUDE_PLUGIN_ROOT}/harness/setup/derive.sh --block <id>
${CLAUDE_PLUGIN_ROOT}/harness/checks/check-project-config.sh
${CLAUDE_PLUGIN_ROOT}/harness/setup/write.sh <id> [--state confirmed|declined] [--because "…"] [--remove key,…] <<'JSON'
{"<top-level key>": <value>}
JSON
```

**`harness.yaml` is written only through `write.sh`** — never with Edit or Write. It edits only
the lines whose values change, keeps every comment, refuses inside a worker or a worktree
(exit 4), and refuses rather than guesses when it cannot anchor an edit (exit 3, with the
YAML fragment to show the owner for pasting). A block whose write was refused stays owed.

**The write comes before the question.** The owner confirms against the real file, a derived
path that does not exist never reaches a question, and a session that dies leaves a resumable
draft. The ledger (`.harness/setup.json`) is written only on the answer, so an interrupted walk
resumes exactly at the block nobody answered.

How a block is shown decides whether this reads as a conversation or a form:

- **Evidence is a path, not an argument** — `python-uv (uv.lock) · node-npm @ web
  (web/package-lock.json)`, one parenthetical. The reasoning lives here, not on screen.
- **Never render an empty heading.** No `OWED: (none)`.
- **Blocks with nothing owed are confirmed in groups** of three or four per `AskUserQuestion`,
  each with its value and evidence. Presented and confirmable; not a round trip apiece.
- **Options are outcomes**, the recommendation first. No "explain" option and no bare "change
  one": a contested line becomes its own question with its real alternatives.
- **A counter and a resumption line at every stop** — `[4/13] written. Next: paths.` and *"Run
  /harness-setup again and it picks up at 5."* The counter is the block's number below.
- **Where declining is legitimate it is an option** — `frameworks`, `ports`, `fidelity`,
  `domain.nouns`. Write the explicit empty answer (`[]`, `{}`, or omit `fidelity`); a decline
  of a whole block is `--state declined --because "…"`, a decline of one key inside a block is
  `--state confirmed --because "<key> declined: …"`.
- **Where there is no basis to propose, propose nothing.** The `security` invariants question
  offers only outcomes — *record the gap*, *leave it owed* — and the owner types the rules into
  the free-text answer. It stays one turn, so the tools this skill pre-approves stay approved.
- **If an answer contradicts the repository, stop and surface it** — the owner says a toolchain
  is unused while its lockfile sits there; a confirmed block changed by hand since. Ask *"the
  repo and you disagree — which is right?"*; resolve it silently in neither direction.

## Openings — computed, never asked

```bash
${CLAUDE_PLUGIN_ROOT}/harness/setup/state.sh [revisit | repair <block>]
```

It returns JSON; act on it in this order:

1. **`refuse`** — the config is stamped for a newer plugin. Say what it says and stop: the
   plugin needs updating, not the config.
2. **`render_first_run`** — no `harness.yaml`. Run `write.sh --render-first-run`: a valid file
   from the template and every derivation, with every illustrative template value removed and
   the template's explanations kept. Then walk the whole queue.
3. **`queue`** — the owed blocks, in order, each with its reasons. Walk them with the protocol.
4. **`notes`** — upgrade notes newer than the oldest review, items split by tag. Read each item
   and decide which block it touches; a block is owed when an item touches it and the block's
   `ledger_at` predates the item's version. **mechanical** items are applied in that block's
   WRITE and reported in its SHOW; **ask the owner** items are owed in it.
5. **`stamp_due`** — finish with the `stamp` block. With an empty queue and `patch-behind`,
   that is the whole run: stamp, ask nothing.
6. **`nothing_owed`** — say so, list any blocks only ever defaulted, and stop.

Openings compose: an upgrade on a repository that also gained a toolchain owes both, in one
walk.

## The blocks

Numbered as the counter shows them. Each says what `derive.sh` brings and what is the owner's.

### `identity`

`name` and `slug`. Derived from the git remote, then `package.json`, `pyproject.toml`, the
README's first heading; the others are offered as candidates. The slug is lowercase letters and
digits only — it interpolates into every per-worker variable, a database name among them, so
it goes first: `beads.prefix` and `swarm.merge_slot` chain off it.

### `stacks`

Which toolchain modules apply, at which root. Derived from discovery: every module, shipped or
project-local, tested at the root and every depth-1 directory by its own markers. Present ones
are adopted where their marker was found (`{name: node-npm, root: web}`); ambiguous ones (only
the language's manifest) are owed.

**Then read `evidence.listing` yourself.** It is each candidate root's files. Code does not
decide what a toolchain is — a list of markers would be the name list this harness rejects —
so name every toolchain in play that **no module covers**, and for each ask the owner:

- **author a module** — the breakout in
  `${CLAUDE_PLUGIN_ROOT}/skills/harness-setup/AUTHOR-STACK.md`, which ends in three proofs
- **decline it** — `declined: {stacks: {<name>@<root>: "<why>"}}`, written by this block. A
  reason is required; it silences the config check's warning for that root only.

**Stacks answer "how do I RUN things"; frameworks answer "how do I WRITE good code".** They are
independent: one toolchain serves many frameworks. Both may be shadowed or extended by
project-local copies under `.harness/stacks/` and `.harness/frameworks/`, which are tracked.

### `frameworks`

Derived candidates are frameworks whose module name is a direct dependency; never written until
confirmed. **Then read `evidence.dependencies`** — each root's direct dependency names — and
name any framework in play that **no module covers** (a module named `nextjs` is not found by
its package `next`, so the reading is yours, not code's). For each: adopt, author, or decline.

**Author one inline** — four fields, `.harness/frameworks/<name>.yaml`, written with Write:
`name` (equal to the filename), `description`, `card` (prohibitions only — see the budget), and
optionally `doctrine_skill`. **No detection key**: one was removed for being inert. A card with
no skill is the default; a doctrine skill is a separate piece of work most adopters never need.
`check-skills.sh` and the card budget are its whole verification.

Confirming this block records each root's dependency names in the ledger; the config check
then warns when a direct dependency appears that was not there — the moment a framework may
have arrived.

**The context budget — read this before writing a card.** A card costs ~250 tokens and no tool
call on every dispatch in its lane; a doctrine skill costs nothing until invoked; a *preloaded*
skill costs its full size on every dispatch in every project. So a rule earns a card slot only
if getting it wrong is both likely and expensive. **Never add a `stack-*` or `framework-*`
skill to an agent's `skills:` frontmatter** — `check-skills.sh` fails if you do.

### `commands`

Each declared stack's commands, proved by running them **now** — the stack-command probe, and
its repair ladder where one has rotted, deriving a replacement from what the repository already
declares (CI, the Makefile, package scripts). Done here, at 4, so a wrong stack decision shows
while the owner is still present. Owed is only what nothing could repair. The pre-flight runs
the same repair on every campaign and records it as `repaired` — not a demotion.

**The runner is a project choice, not a toolchain property**: the module ships a default and
`harness.yaml` overrides it, which is what this block writes.

### `paths`

`paths` (docs, index, adrs, proposed, features, architecture, …) and `layout.roles`. Each role is
kept where its path exists — the current value first, else the template's name. A role under a
name nobody guessed is the owner's; a role the project does not have is omitted, which is an
answer, not a gap.

### `tracker`

`tracker`, `beads.prefix`, `swarm.merge_slot`, and the project's `.gitignore`. Every project has
tasks, so this has a **default rather than an absence**: `beads`. Raise the choice only if the
owner cares.

| | `beads` | `mdfiles` |
|---|---|---|
| needs | the `bd` binary | nothing external |
| the tracked artefact | `issues.jsonl` — one blob | per-task markdown, so a wave's changes **diff reviewably** |
| record ceiling | ~64KB, failing **closed and silently** | none |

**If they choose `mdfiles`, `export` is not optional**: the hot store is gitignored, so without
an export path the backlog lives only on the machine that ran the wave.

The `.gitignore` lines the hot store and the workers need are listed by `derive.sh` as
candidates — only the ones git does not already ignore. **Show them and add them with Edit only
once confirmed**; never silently. Without them the first wave commits every worker's live
claims and worktrees.

### `security`

The longest block, and the only one with nothing to propose. `verifier-security` answers *"what
can the wrong person now reach?"*, which nobody can answer from the code. `derive.sh` brings
boundary-shaped paths and `.env.example` variable **names** as a list to react to — never as
answers. Three questions, in this order, because each narrows the next:

1. **paths** — which paths, if changed, always warrant a security look?
2. **tokens** — which identifiers mark sensitive data in a diff?
3. **invariants** — what must never happen, stated as a rule? Outcomes only: *record the gap*
   (declined, with why) or *leave it owed*; the rules themselves come as free text.

**A missing invariant is a lens that never fires.** If the owner is unsure, record what they do
know and the gap — never a plausible-sounding rule.

### `areas`

The area map, in report order: depth-1 directories ranked by recent churn, labelled by name.
**`triggers: [security]` is applied from the `security` block** — which is why security comes
first; asking areas first means asking twice. Owed is only a label that reads wrong.

### `testing`

`testing` and `lenses`. Derived: where the tests live and the aggregate commands (Makefile,
package scripts). `lenses.additional` defaults to none. **Owed: `gates` and `coverage`** — the
tests that must never be weakened, and the bar. Neither can be inferred.

**One thing config cannot do**: `testing.aggregate_commands` names the runner the wave gate uses,
but a permission rule lives in each orchestrator command's `allowed-tools`, which ships granting
`Bash(make:*)`. A different runner needs adding there too, or it stops on a prompt that, in an
unattended dispatch, waits indefinitely.

### `lanes`

`lanes`, `ports` and `fidelity`. One lane per declared stack plus `docs`, with a conservative cap
whose `constraint:` says it is unmeasured — a real cap is measured on the owner's machine.

**`ports` is declared, not scraped.** `derive.sh` shows candidates with their source line —
compose files, `.env.example`, script flags, what is listening now — and nothing is written
until the owner names the full set. `ports: {}` is an answer: nothing listens.

**Design fidelity — declare it or leave it out.** If the project has a design handover to
compare screens against, add a `fidelity:` block and mark the lanes that do that work with
`fidelity: true`; otherwise omit both and the tooling stays dormant. A fidelity project must
provide `playwright` and `sharp` itself; credentials stay in the environment.

### `domain`

`domain` and `signals`. `nouns` are the words that mean something in this product and nothing
in anyone else's — what the prompt guard checks against, so an empty list means no check, which
is legitimate. **Only unambiguous words**: `fixture` cost 41 false hits for being an entity in one
domain and a test construct in every codebase. `signals` baselines are measured from this
repository's own history and file lengths — never copied.

### `models`

Nothing to write, and say so in one line: strengths, activities and providers take the plugin's
shipped values until the owner has *measured* a reason to patch one, and patching is per key so
an upgrade still reaches the rest. Confirm the acknowledgement; it is never stale.

### `stamp`

Stamp, then prove — the stamp means "reviewed against this version", not "green":

```bash
${CLAUDE_PLUGIN_ROOT}/harness/checks/check-project-config.sh --stamp   # writes harness.version, then checks: OK or WARN
${CLAUDE_PLUGIN_ROOT}/harness/checks/check-skills.sh                    # declarations resolve, preload within budget
${CLAUDE_PLUGIN_ROOT}/harness/checks/check-model-config.sh              # every agent's activity resolves
${CLAUDE_PLUGIN_ROOT}/harness/checks/check-stack-commands.sh            # each stack's declared commands actually work
${CLAUDE_PLUGIN_ROOT}/harness/swarm/probe-worktree.sh <lane>            # per lane: a real worktree a worker can use
```

A failure re-queues the block that owns it — `state.sh repair <block>`. These five are the
consumer's whole verification, run from the consumer's repository as written. **Add nothing to
the owner's repository to run them** — no Makefile, no wrapper: the plugin's own `make` targets
belong to the plugin's checkout, and the harness's own suite refuses to run outside it. The probe *reports* rather than passes when no per-worker variable is
declared — that is a decision about the stacks' `env`, not a green light.

Close by telling the owner what was authored under `.harness/`, and that a module useful beyond
this repository can be upstreamed to the plugin's shipped set.

## Key reference

| key | block | how to get it wrong |
|---|---|---|
| `name`, `slug` | `identity` | a slug with a hyphen or space breaks database names |
| `harness.version` | `stamp` | typing it — a wrong stamp hides an upgrade |
| `stacks`, `declined.stacks` | `stacks`, `commands` | a module whose markers are absent at its root |
| `frameworks`, `declined.frameworks` | `frameworks` | a decline without a reason |
| `paths`, `layout` | `paths` | a path that does not exist |
| `tracker`, `beads`, `swarm` | `tracker` | guessing the prefix |
| `security` | `security` | an invented invariant |
| `areas` | `areas` | an area left out, so its changes group under `other` |
| `testing`, `lenses` | `testing` | gates nobody named |
| `lanes`, `ports`, `fidelity` | `lanes` | a scraped port list |
| `domain`, `signals` | `domain` | a noun that is also a software word |
| `strengths`, `activities`, `providers` | `models` | patching without a measurement |
| `permissions`, `dispatch` | — | setup never writes these |

## What stays project-owned

`CLAUDE.md` — optional, and the owner's. The harness requires nothing from it. **Check it for a
contradiction and report rather than override**: if it states a different task-id or citation
convention, the harness's governs the artefacts the harness checks, but a silent override would
leave two rules and no signal. Never write the harness's conventions into it.

Anything the harness *does* need belongs in `harness.yaml`. If you find yourself telling an agent
a fact in prose that a check could read, that fact wants to be config.
