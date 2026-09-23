# Plan — project-owned model config (redefinable tiers, extensible providers)

Hand this to Claude Code from `plugins/engineering/mad-harness/`. Read
`CLAUDE.md` and `docs/guides/contributing.md` first; every convention there applies.

## Goal

A consuming project can, in its own `harness.yaml`:

1. **Redefine** any tier the plugin ships (`worker`, `strong`, `strategic`) — provider,
   model, effort, budget — without patching the plugin.
2. **Add** new tiers.
3. **Override or extend** the provider set, including redefining `anthropic`.

Motivation: routing tiers at non-Anthropic models (OpenRouter, DeepSeek, local) to cut cost
and to measure whether cheaper models hold up. Today the only way is to patch
`harness/models/tiers.yaml` inside the plugin cache, which no consumer can do.

## Decisions already made — do not relitigate

- **Projects may redefine shipped tiers**, including `strategic`, the policy-forced tier.
  The owner accepts that a project can lower the model a high-risk dispatch lands on. The
  protection moves from prevention to **visibility** (Phase 3).
- **Projects may redefine `anthropic`** or any other provider.
- **Patch semantics, not replacement**: a project supplying only some keys of a tier
  inherits the rest from the plugin, so a plugin upgrade that changes a budget still
  reaches consumers.
- **`default_tier` and `ladder` are project-overridable too.** A project that adds a tier
  has to be able to place it on the ladder, so withholding `ladder` would make the
  add-a-tier case unsatisfiable. `default_tier` follows for symmetry.
- **Scalars and lists replace; maps patch.** `model_tiers` and `providers` merge per key
  (above). `default_tier` is a scalar and `ladder` is an ordered list — both are replaced
  wholesale when the project names them. Merging a list per index is meaningless, and a
  half-merged ladder would route escalation somewhere nobody chose.
- **All four new keys are normative and agent-unwritable.** `model_tiers`, `providers`,
  `default_tier` and `ladder` join `check_commands._NORMATIVE`. An agent able to write a
  provider block could repoint the harness's endpoint; one able to write `ladder` or
  `default_tier` could route its own work down a tier — the same laundering shape
  `permissions` and `tiers` are already protected against.
- **New keys, no rename**: definitions go in a new `model_tiers:` block and providers in a
  new `providers:` block. The existing `tiers:` block (agent → tier name) keeps its current
  meaning. See the appendix if the rename is preferred instead.

## Non-goals

- Changing the tier **selection** precedence (`--tier` > policy > project `tiers:` >
  agent default > `default_tier`). Unchanged.
- Any new client library or SDK. Providers are still reached through Claude Code's
  `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` path.
- Routing anything real at a new provider. `harness/models/probe-compat.sh` still gates
  that, separately.

## The model to hold

Two questions, kept separate in code and in docs:

| | question | answered by |
|---|---|---|
| **Selection** | which tier does this agent run on? | frontmatter `model_tier:`, `default_tier`, project `tiers:`, policy, `--tier` |
| **Definition** | what *is* that tier? | `tiers.yaml`, patched by project `model_tiers:` |

---

## Phase 1 — extract `_validate`, add the merge (behaviour-neutral)

**Files:** `harness/models/resolve.py`, `harness/models/project.py`

1. Split today's `resolve.py::load_config` into a reader and
   `resolve.py::_validate(config)` carrying every existing structural check: each tier has
   `provider`/`model`/`effort`/`max_budget_usd`; each tier's provider is defined;
   `default_tier` exists; `POLICY_FORCED_TIER` exists.
2. New signature: `load_config(path=None, *, merge_project=True)`. It reads `TIERS_FILE`,
   applies the project's overrides when `merge_project`, then runs `_validate` **on the
   merged result**. Validating before the merge would judge a config nobody runs.
3. New `project.py::Project.model_config()` returning `{"providers": {...},
   "model_tiers": {...}, "default_tier": ..., "ladder": [...]}` from `self.raw`, each key
   absent when the project does not name it, validated for shape the way
   `Project.dispatch()` validates its keys (unknown-key refusal included).
4. Merge rules, in `resolve.py`:
   - `providers`: merge by provider name; within a provider, merge `env` per key.
   - `model_tiers`: merge by tier name; within a tier, merge per key.
   - `default_tier`, `ladder`: replace wholesale when present, ignore when absent. Never
     merge — see the decision above.
   - Use `{**plugin, **project}` so a redefined tier keeps its original position and new
     tiers append — `project.py::Project.tiers` reads declaration order as
     weakest-first and must keep working.
5. `project.py` already imports from `resolve.py`. Do the merge with a function-local
   `from .project import load`, as `resolve.py::orchestrator_domains` already does, and
   swallow the no-project case the same way.
6. `check_config.py::main` and `check_config.py::sync` must call
   `load_config(merge_project=False)`. They judge the plugin's shipped defaults against
   agent frontmatter; a project's deliberate redefinition is not drift. This mirrors the
   existing `project_tiers={}` argument and deserves the same note in the docstring.

**Tests** (`harness/tests/test_project_models.py`, new):

- a project redefining a tier's `provider` + `model` changes what `resolve()` returns
- a project patching only `max_budget_usd` inherits provider, model and effort
- a project adding a brand-new tier makes it selectable via the `tiers:` block
- a redefined tier keeps its ladder position; declaration order is preserved
- a project `default_tier` changes where an agent with no `model_tier:` lands, and
  `Resolved.reason` still reads `global default`
- a project `ladder` replaces the plugin's outright — no interleaving — and
  `escalate.py::next_tier` walks the project's order
- a project overriding `anthropic`'s `env` merges per key rather than replacing the map
- `merge_project=False` returns the plugin's shipped tiers even when the project redefines
  every one of them
- no project config at all is today's behaviour, not an error

**Gate:** the whole existing suite must stay green with no edits. If a test needs changing
in this phase, the change is not behaviour-neutral — stop and say so.

---

## Phase 2 — the two refusals

**Files:** `harness/models/project.py`, `harness/models/resolve.py`

1. **`model` and `provider` move together.** A project tier that sets `model` without also
   naming `provider` is refused by name, listing both keys. Restating
   `provider: anthropic` is fine and is the point — the failure being prevented is a
   project pointing a tier at `qwen/...` while the provider silently stays `anthropic`,
   which dispatches to Anthropic with an unknown id and reads as a provider outage.
2. **No literal credentials in a project file.** In a **project-supplied** provider block,
   any `env` key matching `*TOKEN*`, `*KEY*` or `*SECRET*` must be a `${VAR}` reference,
   not a literal. A literal base URL stays legal — `resolve.py::provider_env` already
   supports it and it is not a secret. `harness.yaml` is committed; `tiers.yaml` is
   plugin-owned and reviewed, so this rule applies to the project layer only.
3. Extend `test_model_routing.py::test_no_provider_declares_a_model` to run against the
   **merged** config, so a project cannot reintroduce `ANTHROPIC_MODEL` through the new
   door. Same for `test_no_tier_names_a_bare_model_alias` and
   `test_every_tier_names_a_model_directly`.
4. **Ladder and default validation, on the merged config.** `escalate.py::next_tier`
   already raises on an off-ladder tier, but at dispatch time — and escalation is the one
   path where a silent wrong direction is expensive. So `_validate` enforces, by name:
   - `default_tier` names a tier that exists
   - every `ladder` entry names a tier that exists, with no duplicates
   - every defined tier appears on the ladder — a project adding a tier must place it
   - `POLICY_FORCED_TIER` appears on the ladder, or high-risk work escalates to a tier
     escalation cannot reason about

   `POLICY_FORCED_TIER` is a constant in `resolve.py`, not derived from the ladder, so a
   project can legally put it somewhere other than last. That is allowed, and the config
   check says so out loud (Phase 3) rather than refusing it.

**Tests:** each refusal gets a companion test planting the violation and asserting the
failure names the offending key, per the "every mechanical guard carries a companion test
proving it can fail" convention.

---

### Normative keys

Add `"model_tiers"`, `"providers"`, `"default_tier"` and `"ladder"` to
`check_commands._NORMATIVE`, alongside the existing `"tiers"` entry, each with the comment
convention that block already uses — one line saying what an agent could do with write
access. `check_commands::write_repair` already refuses any key whose first dotted segment
is in the tuple, so no other change is needed there.

**Test:** extend `test_project_tiers.py::test_the_tiers_block_is_normative_so_no_agent_can_move_the_lens_judging_it`
(or add a sibling) to assert `write_repair` refuses each of the four keys by name, and that
a dotted path under them (`providers.openrouter.env.ANTHROPIC_BASE_URL`) is refused too.

---

## Phase 3 — make redefinition loud

This replaces the guarantee being given up. Without it a cost series silently mixes a
project's redefined `worker` with the plugin's, which breaks the premise of the A/B rig.

**Files:** `harness/models/resolve.py`, `harness/models/dispatch.py`,
`harness/models/report.py`, `harness/models/ab_report.py`,
`harness/models/check_project.py`, `docs/concepts/cost.md`

1. `Resolved` gains `tier_source: str` — `"plugin"` or `"project"` — set during the merge.
   Include it in `Resolved.redacted()`.
2. Add it to the telemetry payload in `dispatch.py::Outcome.telemetry`, following
   `docs/guides/contributing.md` § *Add a telemetry metric*: read it back in
   `report.py::summarise`, add it to `ab_report.py::METRICS` so a series can compare on
   it, confirm `make models-cost` still renders, and document the field in
   `docs/concepts/cost.md`.
3. `check_project.py` prints redefinitions next to the existing one-line overrides row:

   ```
   tiers:   verifier-spec->worker  (project overrides)
   models:  worker = openrouter/qwen3-coder-plus  (project redefinition; plugin ships anthropic/claude-sonnet-5)
   ```

   Call out a redefined `strategic` explicitly — it is the policy-forced tier.
4. `dispatch.sh --dry-run` appends `(redefined by project)` to the tier line, and prints
   the effective `default_tier` and `ladder` with their provenance.
5. `check_project.py` prints an overridden `default_tier` or `ladder` on their own row, and
   warns — without refusing — when `POLICY_FORCED_TIER` is not last on the project's
   ladder:

   ```
   routing: default_tier=worker  ladder=[worker, candidate, strong, strategic]  (project)
   ```

   These stay out of the per-dispatch telemetry payload deliberately: they are properties
   of the whole run, not of one dispatch, and a field repeated identically on every event
   is cost without information. `tier_source` already makes the tier a dispatch landed on
   attributable. Note in `docs/concepts/cost.md` that a series run under a project ladder
   is not comparable with one run under the plugin's.

**Tests:** telemetry carries `tier_source: project` for a redefined tier and `plugin`
otherwise; `check_project` prints the redefinition row and is silent without one; a
redefined `strategic` is named in the output.

---

## Phase 4 — the frontmatter mirror stops assuming Anthropic

**Files:** `harness/models/check_config.py`, `harness/tests/test_model_routing.py`

`check_config.py::sync` stamps `model:`/`effort:` into agent frontmatter from the tier. That
is coherent only while every tier is Anthropic: the frontmatter reader is Claude Code's
native Agent tool / `claude --agent`, which has **no provider concept** and would hand a
`qwen/...` id to Anthropic.

1. When a tier's provider is not `anthropic`, `sync()` does not stamp that agent's `model:`,
   and `check_config.py::main` does not report the mismatch as drift. Print the exemption
   rather than hiding it.
2. Exempt the same agents in
   `test_model_routing.py::test_real_frontmatter_matches_the_tier_it_declares`, with the
   reason in the docstring.
3. `check_config.py`'s module docstring says frontmatter is "what actually EXECUTES …
   which is most dispatches". That predates `swarm/guard-agent-tool.sh`, which denies
   `Agent(subagent_type: mad-harness:*)` outright. Correct it in the same commit.

---

## Phase 5 — docs

Obey "two documents must not state the same fact — link, do not restate".

- `docs/reference/harness-yaml.md` — four new rows in the **Blocks** table (`model_tiers`,
  `providers`, `default_tier`, `ladder`), each linking to `models/resolve.py` rather than
  restating the merge rules, and each stating whether it patches or replaces.
- `docs/reference/dispatch.md` — the selection-vs-definition split as its own short
  section above the existing precedence table; add `tier_source` to the telemetry payload
  listing.
- `docs/concepts/agents-and-tiers.md` — the shipped tier table and the escalation ladder
  are now *defaults*; say so.
- `project.py::Project.tiers`' "unknown tier" error message lists the known tiers in
  declaration order. Now that a project can supply its own `ladder`, list them in **ladder**
  order instead — the message reads as weakest-first and should stay true.
- `docs/guides/contributing.md` — a new **Add a provider** section in the same numbered
  shape as the neighbouring ones: the provider block, the tier that reaches it, the `.env`
  entry, `probe-compat.sh`, and what the probe does not cover (it runs `claude -p`
  unsandboxed, so it cannot tell you a sandboxed worker reaches the endpoint).
- `harness/models/tiers.yaml` — the header comment says this file "owns provider, model,
  effort and budget". Amend: it owns the **defaults**; a project may patch them.
- `templates/harness.yaml.example` — a commented-out worked example of both new blocks.
- `harness/.env.example` — an `OPENROUTER_*` pair alongside the DeepSeek one, commented
  out, with the same "the model belongs to the tier" note.
- `docs/upgrading.md` — a note for the version this lands in.
- If `docs/` tables between `GENERATED:` markers or any `docs/assets/src/*.d2` are
  affected, regenerate with `harness/checks/check-docs.sh --write`. Do not hand-edit.

---

## Verification

1. `make check` green — mandatory before any commit.
2. `make models-cost` renders with the new field.
3. Manual end-to-end in a scratch project: `harness.yaml` redefining `worker` at
   `provider: openrouter`, `model: qwen/qwen3-coder-plus`; confirm
   `dispatch.sh <agent> --dry-run` shows the new provider/model and the redefinition note,
   and that `check-project-config.sh` prints the row.
4. Per `CLAUDE.md`, this changes dispatch — run the lab:
   `harness/wavelab/reset.sh`, `dispatch-wave.sh beads`, `merge-wave.sh beads`,
   `compare.sh`. Keep the tier at its shipped Anthropic default for that run: the lab is
   verifying the merge did not break dispatch, not that a new provider works.
5. Bump `.claude-plugin/plugin.json`. Code changes reach dispatched agents immediately via
   `--plugin-dir`, but the installed plugin backs the interactive commands, and
   `wavelab/check-plugin-fresh.sh` refuses a stale run.

## Commit sequence

One commit per phase, each green on `make check`. Commit messages: say what was wrong and
how it was established — measurement beats assertion.

## Open questions for the owner

None outstanding. Every design decision is settled above; if something here proves wrong
once the code is in front of you, stop and say so rather than working around it.

---

## Appendix — if the rename is taken instead

Cleaner long-run naming: `tiers:` in `harness.yaml` becomes the *definitions* block
(matching `tiers.yaml`'s own shape, one mental model) and `agent_tiers:` becomes the
agent → tier map. Do it **first**, as its own commit, not last — otherwise Phase 1's tests
get written twice.

Touches: `project.py::Project.tiers` (rename and split), `check_commands._NORMATIVE`,
`check_project.py`'s output, the Blocks table in `docs/reference/harness-yaml.md`, the
precedence table in `docs/reference/dispatch.md`, `templates/harness.yaml.example`, and
most of `harness/tests/test_project_tiers.py`. No consumers exist yet, so this is the last
cheap moment to do it.
