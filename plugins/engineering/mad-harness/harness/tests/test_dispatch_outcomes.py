"""A terminal error is an outcome, not a crash — and the cache is measured, not guessed.

Two workers killed by the per-dispatch ceiling left output files holding only a Python
traceback: no turns, no tool calls, no cost event. The spend that caused the failure was
the one spend that became invisible, `make models-cost` said "No dispatch telemetry yet"
right after two dispatches spent the whole ceiling, and the orchestrator was left to
interpret a stack trace with the evidence deleted. Separately, the cost analysis had to
reconstruct cache hit rates from transcripts by hand, because nothing recorded them.
"""

from __future__ import annotations

import pytest

from models import report
from models.dispatch import EXIT_BUDGET, dispatch, resolve

KILL = {
    "subtype": "error_max_budget_usd",
    "is_error": True,
    "result": "Reached maximum budget ($1.5)",
    "total_cost_usd": 1.53,
    "num_turns": 34,
    "duration_ms": 412_000,
    "session_id": "dead-1",
    "usage": {
        "input_tokens": 1_000,
        "output_tokens": 9_000,
        "cache_read_input_tokens": 3_700_000,
        "cache_creation_input_tokens": 95_000,
    },
    "permission_denials": [],
    "terminal_reason": "max_budget_usd",
    "transcript": ["Reading the harvest document", "[tool] Read {\"file_path\": \"docs/x.md\"}"],
}


def _runner(payload):
    def run(r, prompt, **kw):
        return payload
    return run


@pytest.fixture(autouse=True)
def _sandboxed(monkeypatch, tmp_path):
    monkeypatch.setattr("models.dispatch.require_sandbox", lambda: None)
    # `main()` keeps every result under RESULT_DIR; in the suite that is the plugin's
    # own tree. Ignored by git, but a test must not write there at all.
    monkeypatch.setattr("models.dispatch.RESULT_DIR", tmp_path / "out")


def test_a_budget_kill_is_a_recorded_outcome_with_its_spend_and_transcript():
    out = dispatch("verifier", "x", runner=_runner(KILL))
    assert not out.ok
    assert out.budget_exhausted and out.terminal == "budget"
    assert out.cost_usd == 1.53 and out.turns == 34, "the spend that caused the kill"
    assert out.transcript == KILL["transcript"], "what arrived before the kill survives"
    t = out.telemetry(task="T-1")
    assert t["terminal"] == "budget" and t["cost_usd"] == 1.53


def test_the_sdk_loop_returns_a_terminal_errors_payload_instead_of_raising(monkeypatch):
    """The real seam: `query()` raises ResultError after streaming some turns. The loop
    must hand back the payload the CLI reported plus the turns it kept, not propagate."""
    from claude_agent_sdk import AssistantMessage, ResultError, TextBlock, ToolUseBlock

    from models import dispatch as mod

    async def fake_query(prompt, options):
        yield AssistantMessage(content=[TextBlock(text="Reading the harvest document")], model="m")
        yield AssistantMessage(content=[ToolUseBlock(id="1", name="Read", input={"file_path": "docs/x.md"})], model="m")
        raise ResultError(
            "Reached maximum budget ($1.5)",
            data={"subtype": "error_max_budget_usd", "total_cost_usd": 1.53, "num_turns": 34,
                  "usage": {"input_tokens": 1, "output_tokens": 2}, "session_id": "dead-1"},
            exit_code=1,
        )

    monkeypatch.setattr("claude_agent_sdk.query", fake_query)
    monkeypatch.setattr(mod, "broker", lambda *a, **k: None)
    r = resolve("verifier")
    payload = mod._run_sdk(r, "prompt", cwd=".", env={}, timeout=30)
    assert payload["subtype"] == "error_max_budget_usd" and payload["is_error"]
    assert payload["total_cost_usd"] == 1.53 and payload["num_turns"] == 34
    assert payload["transcript"] == ["Reading the harvest document", '[tool] Read {"file_path": "docs/x.md"}']


def test_main_prints_the_partial_transcript_names_the_kill_and_exits_distinctly(monkeypatch, tmp_path, capsys):
    from models import dispatch as mod

    monkeypatch.setattr(mod, "_run_sdk", lambda *a, **k: KILL)
    monkeypatch.setattr(mod, "record", lambda *a, **k: True)
    pf = tmp_path / "p.txt"
    pf.write_text("do the thing")
    rc = mod.main(["verifier", "--prompt-file", str(pf), "--task", "T-1"])
    out, err = capsys.readouterr()
    assert rc == EXIT_BUDGET and rc not in (0, 1)
    assert "partial transcript, 2 step(s)" in out and "Reading the harvest document" in out
    assert "Reached maximum budget" in out, "the error is appended, not substituted"
    assert "BUDGET EXHAUSTED: $1.53" in err and "resume-point.sh" in err and "not BLOCKED" in err


def test_cache_shares_are_measured_per_dispatch():
    out = dispatch("verifier", "x", runner=_runner(KILL))
    assert out.prompt_tokens == 3_796_000
    assert out.cache_hit_pct == 97.5 and out.cache_write_pct == 2.5
    t = out.telemetry()
    assert t["cache_hit_pct"] == 97.5 and t["cache_write_pct"] == 2.5


def test_an_empty_dispatch_has_no_cache_rate_rather_than_a_division_error():
    payload = {**KILL, "usage": {}}
    out = dispatch("verifier", "x", runner=_runner(payload))
    assert out.prompt_tokens == 0 and out.cache_hit_pct is None and out.cache_write_pct is None


def test_the_report_weights_the_cache_by_tokens_and_counts_kills():
    """One cold expensive run and one warm cheap one: a per-dispatch average would say
    50%; the token-weighted answer is what the bill reflects."""
    events = [
        {"agent": "fullstack-engineer", "tier": "worker", "provider": "a", "ok": False, "terminal": "budget",
         "cost_usd": 1.5, "turns": 30, "input_tokens": 0, "cache_creation_tokens": 900_000, "cache_read_tokens": 100_000},
        {"agent": "fullstack-engineer", "tier": "worker", "provider": "a", "ok": True, "terminal": "success",
         "cost_usd": 0.2, "turns": 5, "input_tokens": 0, "cache_creation_tokens": 0, "cache_read_tokens": 100_000},
    ]
    (row,) = report.summarise(events)
    assert row["budget_kills"] == 1
    assert row["cache_hit_pct"] == 18 and row["cache_write_pct"] == 82
    assert row["fail_pct"] == 50


def test_an_api_error_result_is_not_success_and_a_closed_usage_window_is_named():
    """`subtype: success` with `is_error: true` is the API-failure shape; the usage window
    closing arrives that way with "You've hit your session limit" as the result, one turn,
    $0. Read as success, an A/B series recorded 26 runs of it."""
    limited = {**KILL, "subtype": "success", "is_error": True, "total_cost_usd": 0.0, "num_turns": 1,
               "result": "You've hit your session limit · resets 2:20am", "terminal_reason": None}
    out = dispatch("verifier", "x", runner=_runner(limited))
    assert not out.ok and out.terminal == "usage_limit"
    other = {**limited, "result": "API Error: 529 overloaded", "terminal_reason": "api_error"}
    assert dispatch("verifier", "x", runner=_runner(other)).terminal == "api_error"
    fine = {**limited, "is_error": False, "result": "PASS"}
    assert dispatch("verifier", "x", runner=_runner(fine)).terminal == "success"
