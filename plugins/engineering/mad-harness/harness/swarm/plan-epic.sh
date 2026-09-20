#!/usr/bin/env bash
# campaign-loop §3 as a sequencer around its five judgement dispatches: spec-index status →
# survey (full / DELTA + stamp / reuse) → the ADEQUACY note and the staged spec index → the
# ABSENT path (draft, audit, or REQUIREMENT + park) → fold-in ① (register gate, spec-editor)
# → architect (design or sanity-check; ADEQUACY: ABSENT parks) → the design staged with a
# draft decision record per DECISION: line → planner (with the next ADR number) → analyst
# audit (a FAIL re-dispatches the planner fresh with the findings; a second FAIL files the
# requirement and parks) → the gate → apply-plan.sh. The state file makes --from a resume.
#
#   harness/swarm/plan-epic.sh <epic> [--mode auto|interactive] [--from <stage>] [--triage UNPLANNED|PARTIAL|READY] [--reset]
#
# Exit 0 planned and applied · 1 a stage failed · 2 could not judge (a dispatch returned no
# verdict — nothing approved) · 4 parked · 6 interactive stop: an approval is owed, the report
# names the artefact and the --from that continues.
set -euo pipefail
export MAD_HARNESS_CALLER_PWD="${MAD_HARNESS_CALLER_PWD:-$PWD}"
cd "$(dirname "$0")/.."
exec env -u VIRTUAL_ENV uv run python -m models.plan_epic "$@"
