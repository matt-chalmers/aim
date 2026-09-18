<!-- ORCHESTRATOR CARD: the canonical copy. Mirrored into every command under commands/
     and printed after a compaction or resume by swarm/pinned.sh; check-orchestrator-card.sh
     fails if any copy differs from this file or exceeds the card budget. Edit here. -->
**You are the most expensive caller in the system.** Measured: a campaign orchestrator's context averaged ~210k tokens during the campaign and ~380k over its session, so every tool call re-reads it — three to six times what the same call costs a worker. Three rules follow:

1. **Never load reference material into yourself.** A spec corpus, an API reference, a research body: a built-in agent (`Agent(subagent_type="general-purpose")`) loads it, answers your question, and dies with it. Measured: one reference skill loaded here cost $11.21 re-sent over the 64 turns that followed; the same load in a subagent, ~$2.
2. **One call where five would do.** `preflight.sh`, `apply-plan.sh`, `close-epic.sh` are whole sequences; `scan.sh`, `peek.sh`, `run.sh` batch reads and runs; ask `tk.sh` once with `--json`, not five times.
3. **Artefacts by path.** `dispatch.sh … --digest` and `tk.sh note --file`: a subagent's result goes from its file to whatever consumes it, never through you.

mad-harness agents run through `dispatch.sh` — a hook refuses the Agent tool for them; built-in agents are for delegated reading.
<!-- END ORCHESTRATOR CARD -->
