"""The tool-result volume of a dispatch, read back from its transcript.

Field workers carried 28% of their prompt as their own tool results and nothing
recorded it; the lab's carry 6%, so the lab cannot show the lever the field analysis
ranks second. The measurement has to come from the field, which means from here.
"""

from __future__ import annotations

import json

from models import transcript as mod


def _session(path, events):
    with path.open("w") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")


def _assistant(*content):
    return {"type": "assistant", "message": {"role": "assistant", "content": list(content)}}


def _user(*content):
    return {"type": "user", "message": {"role": "user", "content": list(content)}}


def _use(i, name):
    return {"type": "tool_use", "id": i, "name": name, "input": {}}


def _result(i, text):
    return {"type": "tool_result", "tool_use_id": i, "content": [{"type": "text", "text": text}]}


def test_a_result_is_weighed_by_the_turns_that_re_read_it(tmp_path):
    """8k chars fetched at turn 1 of 4 is carried by turns 2, 3, 4: 3 × 8k ÷ 4 tokens.
    The same 8k at the last turn is carried by nobody."""
    p = tmp_path / "s.jsonl"
    _session(p, [
        _assistant(_use("a", "Read")), _user(_result("a", "x" * 8_000)),
        _assistant(_use("b", "Bash")), _user(_result("b", "y" * 100)),
        _assistant({"type": "text", "text": "thinking"}),
        _assistant(_use("c", "Bash")), _user(_result("c", "z" * 8_000)),
    ])
    v = mod.result_volume(p)
    assert v.results == 3 and v.chars == 16_100 and v.large == 2
    assert v.carried_tokens == (8_000 * 3 + 100 * 2 + 8_000 * 0) // 4
    assert v.by_tool == {"Read": 8_000, "Bash": 8_100}
    assert v.telemetry()["result_chars_by_tool"] == {"Bash": 8_100, "Read": 8_000}


def test_a_string_result_and_a_broken_line_are_both_survivable(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(
        json.dumps(_assistant(_use("a", "Grep"))) + "\n"
        + "{not json\n"
        + json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "a", "content": "plain"}]}}) + "\n"
    )
    v = mod.result_volume(p)
    assert v.results == 1 and v.chars == 5 and v.by_tool == {"Grep": 5}


def test_the_transcript_is_found_by_the_clis_slug_of_the_cwd_or_not_at_all(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    cwd = tmp_path / "repo" / ".claude" / "worktrees" / "w1"
    cwd.mkdir(parents=True)
    slug = str(cwd.resolve()).replace("/", "-").replace(".", "-")
    d = tmp_path / "cfg" / "projects" / slug
    d.mkdir(parents=True)
    _session(d / "sess-1.jsonl", [_assistant(_use("a", "Bash")), _user(_result("a", "ok"))])
    assert mod.session_file(cwd, "sess-1") == d / "sess-1.jsonl"
    assert mod.result_volume_for(cwd, "sess-1").chars == 2
    # A moved transcript is still found by its session id; an unknown one is None, not 0.
    assert mod.session_file(tmp_path / "elsewhere", "sess-1") == d / "sess-1.jsonl"
    assert mod.result_volume_for(cwd, "sess-none") is None
    assert mod.session_file(cwd, "") is None


def test_a_dispatch_records_its_result_volume_and_absence_is_absence(tmp_path, monkeypatch):
    from models.dispatch import dispatch

    monkeypatch.setattr("models.dispatch.require_sandbox", lambda: None)
    monkeypatch.setattr("models.levers._project_block", lambda: {})
    payload = {"subtype": "success", "is_error": False, "result": "ok", "total_cost_usd": 0.1,
               "num_turns": 2, "duration_ms": 1, "session_id": "sess-9", "usage": {}, "permission_denials": []}
    monkeypatch.setattr("models.transcript.result_volume_for", lambda cwd, sid: None)
    t = dispatch("verifier", "x", runner=lambda *a, **k: payload).telemetry()
    assert "carried_result_tokens" not in t and "tool_result_chars" not in t

    seen = {}

    def found(cwd, sid):
        seen["args"] = (cwd, sid)
        return mod.ResultVolume(results=2, chars=9_000, large=1, carried_tokens=2_250, by_tool={"Read": 9_000})

    monkeypatch.setattr("models.transcript.result_volume_for", found)
    t = dispatch("verifier", "x", runner=lambda *a, **k: payload).telemetry()
    assert seen["args"][1] == "sess-9"
    assert t["tool_result_chars"] == 9_000 and t["large_results"] == 1 and t["carried_result_tokens"] == 2_250


def test_the_cost_report_shows_results_as_a_share_of_the_prompt_and_dash_when_unmeasured():
    from models.report import summarise

    rows = summarise([
        {"agent": "a", "tier": "worker", "provider": "p", "cost_usd": 1, "turns": 10, "ok": True,
         "cache_read_tokens": 90_000, "cache_creation_tokens": 5_000, "input_tokens": 5_000,
         "carried_result_tokens": 28_000, "large_results": 3},
        {"agent": "a", "tier": "worker", "provider": "p", "cost_usd": 1, "turns": 10, "ok": True,
         "cache_read_tokens": 100_000, "cache_creation_tokens": 0, "input_tokens": 0},
        {"agent": "b", "tier": "worker", "provider": "p", "cost_usd": 1, "turns": 10, "ok": True,
         "cache_read_tokens": 100_000, "cache_creation_tokens": 0, "input_tokens": 0},
    ])
    by = {r["agent"]: r for r in rows}
    # Only the measured dispatch counts toward the share — an unmeasured one is not a zero.
    assert by["a"]["result_share_pct"] == 28 and by["a"]["large_results"] == 3
    assert by["b"]["result_share_pct"] is None and by["b"]["large_results"] == 0
