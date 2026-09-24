# `wavelab/` — real waves, two backends, one base

Everything else in this tree tests the harness against itself or against a scratch
directory. This runs **real dispatched agents** against **two identical repositories** —
one on `beads`, one on `mdfiles` — and diffs the outcomes.

**The differential is the point.** Either backend can be tested alone, and the
conformance contract already does that. What it cannot see is a divergence the contract
never thought to assert: two backends that both pass every stated rule and still behave
differently under a real wave. Identical base + identical work + different backend means
any difference in the outcome is a finding.

**Outside this repository, deliberately.** The harness is a plugin operating on a foreign
checkout, and both of the path bugs found during the tracker port were the plugin
resolving something against its own tree instead of the consumer's. A test repo living
inside this one would not exercise that at all.

**And pointed at by standing in it, never by telling it.** The lab scripts `cd` into the
target repository and call the harness from there; nothing here sets `MAD_HARNESS_REPO`
or `MAD_HARNESS_CALLER_PWD`. They used to export `MAD_HARNESS_REPO`, and every wrapper —
and every agent `dispatch.py` spawned, since it inherits the environment — honoured that
ahead of resolving anything. The lab was outside the tree, shaped like a real project,
and still could not see a resolver that returned the plugin's own directory, because it
had answered the resolver's one question for it. 0.9.0 passed every wave here while a
real project got an empty backlog with exit 0. The only way to test that a tool finds its
target is to not tell it where the target is.

## The trap this found before dispatching anything

A dispatched agent is resolved **by name against the installed plugin**, never against
this checkout. The installed copy here was five days and twenty-five commits stale — it
predated the whole tracker port — so a live wave would have run the OLD prompts and looked
like a valid test of the new ones.

`claude plugin update` does not catch it: for a directory-sourced plugin it compares
**version strings, not content**, and reports "already at the latest version" while the
cache and the tree differ by any number of commits. **Bump `version` in
`.claude-plugin/plugin.json`, then update.** `check-plugin-fresh.sh` refuses the run
otherwise, and `reset.sh` calls it first.

```bash
harness/wavelab/reset.sh          # build or rebuild both repos from the base
harness/wavelab/dispatch-wave.sh beads     # dispatch real workers on the ready set
harness/wavelab/dispatch-wave.sh mdfiles
harness/wavelab/compare.sh        # diff what the two runs produced
```

## What it does and does not test

**Does:** that a dispatched agent can reach `tk.sh` through the boundary, that
`.swarm-env` carries a usable identity, that a worker's claim excludes its sibling in a
real wave, that both backends produce the same observable outcome from the same work, and
that the tracked export and the epic view are correct afterwards.

**Does not:** `/swarm`'s full doctrine — the shared-vocabulary and new-file checks, the
routing of a FAIL, the circuit breakers' actions. Those are the orchestrator's judgement, and
an agent reading `/swarm` is what exercises them. (The unanimity rule — any FAIL blocks — is
a boolean, not a judgement; it was called judgement here until the 0.10.19 audit.) This tests the tracker under real agents, which is the
part no other test reaches.

## The base

A small uv/pytest project, because the wave gate has to mean something: a worker must be
able to run a real suite and a lens must be able to judge it.

One epic, three tasks, shaped to produce a two-wave DAG:

| task | touches | depends on |
|---|---|---|
| `normalise_email` | `src/wavelab/email.py` | — |
| `normalise_phone` | `src/wavelab/phone.py` | — |
| wire both into `clean_contact` | `src/wavelab/contact.py` | both of the above |

Wave 1 is the two file-disjoint tasks in parallel — which is what tests the claim mutex
under real concurrency. Wave 2 is the one that depends on them, which tests that closing a
blocker actually releases its dependent.

## Driving an interactive command

`claude -p` and the SDK cover every dispatched agent; `dispatch-wave.sh` covers a wave. An
**interactive** command cannot be reached either way — `/design` stops at an
`AskUserQuestion`, and agent teams do not spawn teammates in `-p` at all — so
`drive-interactive.py` runs a real session in a pty and answers the TUI's dialogs:

```bash
harness/wavelab/reset.sh --only beads
cd ~/harness-wavelab/beads
printf '/mad-harness:design <epic>\n' > /tmp/p.md
python3 <this repo>/harness/wavelab/drive-interactive.py "$PWD" 1600 /tmp/p.md \
  --plugin-dir <the plugin> --settings <scratch settings disabling the installed copy>
```

Read the session transcript under `~/.claude/projects/<slug>/` — and its `subagents/`
directory, which is where a teammate's cost lives, since the harness records none for them
— plus `checks/session-cost.sh` on the lead. Never read the screen log: it is a terminal's
redraw. Answering a dialog is a decision a person would have made, so this belongs in the
lab and nowhere near a real repository. It measured the 0.10.31 agent-teams arms (`docs/upgrading.md`).

## A/B-ing a cost lever

Every cost lever in `harness/models/levers.py` is a switch, off until measured. This is
where it gets measured: the same seeded epic, N runs with the lever off and N with it on,
each in a fresh repository, every dispatch event tagged `lever:arm:run`.

```bash
harness/wavelab/ab.sh static_prefix --runs 5 --fanout 8 --wave1-only   # the fan-out lever, at the size the analysis computed
harness/wavelab/ab.sh cache_ttl     --runs 5 --wave1-only
harness/wavelab/ab.sh task_budget   --runs 5                            # needs the whole epic: it is about finishing
harness/wavelab/ab.sh preload --runs 5 --lenses                       # a candidate skill in the writers' system prompt, judged
harness/wavelab/ab-report.sh static_prefix                              # medians, IQRs, and whether the spreads separate
```

Levers the rig knows: `cache_ttl`, `static_prefix`, `stagger`, `task_budget`, `preload`,
`lean_catalog` — the arms are in `ab.sh`'s header. `--lenses` judges every landed task
with the three lenses so an arm that is cheaper by doing less of the doctrine shows as a
lower first-pass rate. What each measured,
and which defaults moved on it, is the lever table in [cost](../../docs/concepts/cost.md).

**Two codes, not two environments: `release`.** Its arms are commits — `off` is the
plugin at `--pin-off` (default 0.10.18, the last release before the prose-to-code
series), `on` is HEAD — each dispatching from its own frozen tree. With `--orchestrated`
the arm is not the shell wave loop but one headless `campaign-orchestrator` running the
whole epic through that tree's `campaign.sh`; its dispatch event's `turns` and `cost_usd`
are the orchestrator's own price for the epic, the number the series claims to cut and
the one the shell loop cannot measure because it has no orchestrator. `ab-report.sh`
prints that block beside the per-dispatch metrics, with the tracker's end state per run.

```bash
harness/wavelab/ab.sh release --orchestrated --runs 1      # orchestrator turns and $ per epic, 0.10.18 vs HEAD
harness/wavelab/ab.sh release --lenses --runs 2            # the dispatched agents' cost and first-pass rate, same two trees
```

**Frozen code.** `ab.sh` checks the plugin out at HEAD into a worktree under the series
root and dispatches every run from that copy; each run records the commit (`.ab-sha`) and
the report flags an arm that mixes them. Edits to the live tree mid-series therefore
change nothing — and the fresh check that aborted the first series is skipped for the
frozen copy (`WAVELAB_SKIP_FRESH=1`). A finished run carries `.ab-done`, so a series can be
relaunched after an interruption and skips what it has.

**A closed usage window stops the series.** A dispatch whose result is "You've hit your
session limit" is `terminal: usage_limit`, never a success; `ab.sh` discards that run and
exits 5, and `series.sh` stops on it. 26 such runs were once recorded as passes.

**Where the lab cannot look.** The lab has no orchestrator — `dispatch-wave.sh` is a
script — so orchestrator numbers come from field transcripts (`checks/session-cost.sh`),
and a lever whose effect is on the orchestrator's context cannot be sized here. The lab's
workers also carry ~7% of their prompt as tool results against 28% in the field, so the
result-volume levers are field measurements too.

**Why not the field.** A campaign runs different tasks every time and the analysis that
motivated this measured 30x token variance on IDENTICAL tasks. The field confirms a
lever's direction; only the same work, repeated, can size it. `ab-report.sh` prints
medians with interquartile ranges and refuses to call overlapping spreads a finding.

**Fan-out.** `--fanout N` seeds N file-disjoint tasks (up to eight normalisers) and sets the
lane cap to match, so wave 1 dispatches N workers at once. The cache levers' whole effect
is on workers 2..N — two workers show the direction, eight show the size the cost analysis
computed (`N × 1.25P` cold against `1.25P + 0.1(N−1)P` warm; 5× at N=8).

**Cost.** Measured on 0.9.x at fan-out 2: ~$0.55–1.50 per dispatch, ~$2.50–3 per two-wave
run. `--wave1-only` halves that and is enough for the cache levers.
