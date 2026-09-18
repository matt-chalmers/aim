"""What one session cost in context, read from its transcript.

A field orchestrator ran 237 requests, 55k → 920k tokens of context, filled 35% by its own
outputs, 35% by injected text and 7% by tool results — and every one of those figures was
counted by hand, for $69. The same rows, the same method, as a script.
"""

from __future__ import annotations

import json

from models import session_cost as mod


def _session(path, rows):
    with path.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _request(rid, ts, read, wrote, fresh, out, *content):
    return {
        "type": "assistant", "requestId": rid, "timestamp": ts,
        "message": {
            "role": "assistant", "id": rid,
            "usage": {"cache_read_input_tokens": read, "cache_creation_input_tokens": wrote, "input_tokens": fresh, "output_tokens": out},
            "content": list(content),
        },
    }


def _use(i, name, **inp):
    return {"type": "tool_use", "id": i, "name": name, "input": inp}


def _result(i, text):
    return {"type": "tool_result", "tool_use_id": i, "content": [{"type": "text", "text": text}]}


def _user(*content, meta=False):
    row = {"type": "user", "message": {"role": "user", "content": list(content)}}
    if meta:
        row["isMeta"] = True
    return row


def _user_str(s, **flags):
    """A user row whose content is a plain string — how a task-notification and a slash
    command land, as against the text blocks a skill load or a typed prompt lands in."""
    return {"type": "user", "message": {"role": "user", "content": s}, **flags}


def _text(s):
    return {"type": "text", "text": s}


SKILL = "Base directory for this skill: /plugins/x/skills/campaign-loop\n\n# Campaign loop\n\nIterate the queue. " + "rule " * 8_000
NOTE = "<task-notification>\n<task-id>a1</task-id>\n<status>completed</status>\n<summary>Agent \"Audit the plan\" finished</summary>\n<output>" + "finding " * 3_000 + "</output>"


def _orchestrator(path):
    """Six requests. A skill lands before request 2 (+12k of context), a tool result of
    2k before 3, a subagent's result then its notification before 4 (+18k), the model's
    own long answer at 4 persists into 5 (+20k), and nothing much before 6."""
    _session(path, [
        _user(_text("/campaign"), _text("start the campaign")),
        _request("r1", "2026-09-16T00:00:00Z", 0, 50_000, 25, 300, _use("a", "Skill", skill="campaign-loop")),
        _user(_result("a", "Launching skill"), _text(SKILL), meta=True),
        _request("r2", "2026-09-16T00:00:30Z", 50_025, 12_000, 20, 400, _use("b", "Bash", command="git status --short && tk.sh ready")),
        _user(_result("b", "M harness.yaml\n" * 500)),
        _request("r3", "2026-09-16T00:01:00Z", 62_045, 3_000, 15, 500, _use("c", "Agent", description="Audit the plan", prompt="long prompt")),
        _user(_result("c", "Agent finished; see the notification")),
        _user_str(NOTE),
        _request("r4", "2026-09-16T00:12:00Z", 65_060, 18_000, 10, 21_000, _text("a very long plan")),
        _user(_text("go on")),
        _request("r5", "2026-09-16T00:13:00Z", 83_070, 20_000, 30, 200, _use("d", "Read", file_path="/repo/docs/plan.md")),
        _user(_result("d", "# plan\n")),
        _request("r6", "2026-09-16T00:13:20Z", 103_100, 100, 5, 100, _text("done")),
    ])


def test_the_counts_are_the_field_methods_counts(tmp_path):
    """Context = cache read + cache written + fresh input of a request; output persists;
    injected is every user-turn text including the meta rows; tool results are chars ÷ 4."""
    p = tmp_path / "s.jsonl"
    _orchestrator(p)
    c = mod.read(p)
    assert [r.context for r in c.requests] == [50_025, 62_045, 65_060, 83_070, 103_100, 103_205]
    assert c.output_tokens == 300 + 400 + 500 + 21_000 + 200 + 100
    injected = {i.head[:5]: i for i in c.injected}
    assert set(injected) == {"/camp", "skill", "task-", "go on"}
    assert injected["/camp"].tokens == len("/campaignstart the campaign") // 4 and injected["/camp"].request == 1
    assert injected["skill"].meta and injected["skill"].request == 2
    assert injected["task-"].tokens == len(NOTE) // 4 and not injected["task-"].meta and injected["task-"].request == 4
    assert c.injected_tokens == sum(len(t) // 4 for t in ("/campaignstart the campaign", SKILL, NOTE, "go on"))
    # The totals come from transcript.result_volume; the per-result items from this reader.
    # Both must see the same tool results, or the top-N table would not add up to the total.
    chars = sum(len(t) for t in ("Launching skill", "M harness.yaml\n" * 500, "Agent finished; see the notification", "# plan\n"))
    assert c.tool_result_tokens == chars // 4 and c.volume.results == len(c.results) == 4
    assert [(r.tool, r.head) for r in c.results] == [
        ("Skill", "campaign-loop"),
        ("Bash", "git status --short && tk.sh ready"),
        ("Agent", "Audit the plan"),
        ("Read", "/repo/docs/plan.md"),
    ]
    assert c.results[1].request == 3 and c.results[1].tokens == len("M harness.yaml\n" * 500) // 4


def test_a_jump_is_a_step_over_the_threshold_and_names_what_landed(tmp_path):
    """Request 4 grew by 18k: the notification. Request 5 grew by 20k with nothing injected
    but the model's own 21k answer at 4 — the output persists. Request 2 grew 12k: under
    the line. The threshold is a parameter, so the line can be lowered to see 2."""
    p = tmp_path / "s.jsonl"
    _orchestrator(p)
    c = mod.read(p)
    jumps = c.jumps()
    assert [(j.request, j.size) for j in jumps] == [(4, 18_010), (5, 20_030)]
    assert jumps[0].landed["kind"] == "injected" and jumps[0].landed["head"].startswith("task-notification: Agent \"Audit the plan\"")
    assert jumps[1].landed == {"kind": "output", "tokens": 21_000, "head": "of request 4"}
    assert [j.request for j in c.jumps(10_000)] == [2, 4, 5]
    assert c.jumps(10_000)[0].landed["head"].startswith("skill campaign-loop:") and c.jumps(10_000)[0].landed["meta"]
    out = mod.render(c)
    assert "+18,010" in out and "+20,030" in out and "+12,020" not in out
    assert "00:12Z" in out.split("landed")[1].splitlines()[1]


def test_a_drop_is_reported_as_a_jump_too_because_it_is_a_compaction(tmp_path):
    p = tmp_path / "s.jsonl"
    _session(p, [
        _request("r1", "2026-09-16T00:00:00Z", 0, 400_000, 0, 100),
        _user_str("This session is being continued from a previous conversation. " + "Summary. " * 6_000, isCompactSummary=True),
        _request("r2", "2026-09-16T00:01:00Z", 0, 60_000, 0, 100),
    ])
    [j] = mod.read(p).jumps()
    assert j.request == 2 and j.size == -340_000 and j.landed["head"].startswith("This session is being continued")
    assert j.landed["kind"] == "injected" and j.landed["tokens"] == len("This session is being continued from a previous conversation. " + "Summary. " * 6_000) // 4
    assert "-340,000" in mod.render(mod.read(p))


def test_rows_of_one_request_are_one_request_and_the_last_row_wins(tmp_path):
    """The CLI writes one row per content block, all sharing a requestId, and re-writes a
    message as streaming progresses; the last row carries the complete usage. Rows that
    read nothing — a rate limit, an expired login — and rows without usage are not requests,
    and a sidechain row belongs to a subagent."""
    p = tmp_path / "s.jsonl"
    _session(p, [
        _request("r1", "2026-09-16T00:00:00Z", 0, 10_000, 5, 40, {"type": "thinking"}),
        _request("r1", "2026-09-16T00:00:01Z", 0, 10_000, 5, 90, _use("a", "Bash", command="ls")),
        _request("r1", "2026-09-16T00:00:02Z", 0, 10_000, 5, 120, _use("b", "Bash", command="pwd")),
        _user(_result("a", "x"), _result("b", "y")),
        {"type": "assistant", "requestId": "err", "message": {"content": [_text("You've hit your session limit")]}},
        _request("limit", "2026-09-16T00:00:03Z", 0, 0, 0, 0, _text("You've hit your session limit")),
        {"type": "assistant", "requestId": "side", "isSidechain": True, "message": {"usage": {"cache_read_input_tokens": 1}, "content": []}},
        _request("r2", "2026-09-16T00:00:04Z", 10_005, 200, 3, 10),
    ])
    c = mod.read(p)
    assert [(r.index, r.context, r.output) for r in c.requests] == [(1, 10_005, 120), (2, 10_208, 10)]
    assert c.summary()["requests"] == 2 and c.jumps() == []


def test_the_top_n_are_the_largest_and_top_is_a_flag(tmp_path, capsys):
    p = tmp_path / "s.jsonl"
    _orchestrator(p)
    s = mod.read(p).summary(top=2)
    assert [i["head"][:19] for i in s["top_injected"]] == ["skill campaign-loop", "task-notification: "]
    assert [r["tool"] for r in s["top_results"]] == ["Bash", "Agent"]
    assert mod.main([str(p), "--top", "1"]) == 0
    out = capsys.readouterr().out
    body = out.split("top 1 injected texts")[1].split("top 1 tool results")[0]
    rows = [ln for ln in body.splitlines() if ln.strip()]
    assert len(rows) == 2 and "skill campaign-loop" in rows[1]  # the header row and one item


def test_the_json_shape_is_the_summary_and_carries_no_dollar_figure(tmp_path, capsys):
    p = tmp_path / "s.jsonl"
    _orchestrator(p)
    assert mod.main([str(p), "--json", "--top", "3"]) == 0
    s = json.loads(capsys.readouterr().out)
    assert s["transcript"] == str(p) and s["requests"] == 6
    assert s["context"] == {"first": 50_025, "last": 103_205, "average": (50_025 + 62_045 + 65_060 + 83_070 + 103_100 + 103_205) // 6, "max": 103_205}
    assert s["first_at"] == "2026-09-16T00:00:00Z" and s["last_at"] == "2026-09-16T00:13:20Z"
    assert set(s) == {
        "transcript", "requests", "first_at", "last_at", "context", "output_tokens", "injected_tokens",
        "tool_result_tokens", "share_of_last_context_pct", "cache_breaks", "rewritten_tokens",
        "jump_threshold", "jumps", "top_injected", "top_results",
    }
    assert s["jump_threshold"] == 15_000 and [j["request"] for j in s["jumps"]] == [4, 5]
    assert set(s["jumps"][0]) == {"request", "at", "size", "context", "landed"}
    assert set(s["top_injected"][0]) == {"request", "tokens", "meta", "head"} and len(s["top_injected"]) == 3
    assert set(s["top_results"][0]) == {"request", "tokens", "tool", "head"}
    assert s["share_of_last_context_pct"]["output"] == round(100 * s["output_tokens"] / 103_205)
    assert "$" not in json.dumps(s) and "usd" not in json.dumps(s).lower()
    # The text report says why there is no dollar figure, and what each number means.
    assert mod.main([str(p)]) == 0
    out = capsys.readouterr().out
    for phrase in ("Tokens, not dollars", "prices differ per model", "persisting", "skills, hooks, task-notifications", "A jump is a load"):
        assert phrase in out
    assert "$" not in out.split("Tokens, not dollars")[0]


def test_a_session_id_is_resolved_the_way_the_cli_files_it_and_an_unknown_one_is_exit_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    cwd = tmp_path / "repo"
    cwd.mkdir()
    monkeypatch.setenv("MAD_HARNESS_CALLER_PWD", str(cwd))
    slug = str(cwd.resolve()).replace("/", "-").replace(".", "-")
    d = tmp_path / "cfg" / "projects" / slug
    d.mkdir(parents=True)
    _orchestrator(d / "sess-7.jsonl")
    assert mod.resolve_transcript("sess-7") == d / "sess-7.jsonl"
    assert mod.main(["sess-7", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["transcript"] == str(d / "sess-7.jsonl")
    assert mod.main(["sess-none"]) == 2
    assert "sess-none" in capsys.readouterr().err
    # A transcript with nothing in it is not a measurement.
    (tmp_path / "empty.jsonl").write_text("{not json\n")
    assert mod.main([str(tmp_path / "empty.jsonl")]) == 1
    assert "no requests" in capsys.readouterr().err


def test_the_wrapper_forwards_to_the_module_and_is_executable():
    import os

    from models.resolve import PLUGIN_ROOT

    sh = PLUGIN_ROOT / "harness" / "checks" / "session-cost.sh"
    assert os.access(sh, os.X_OK)
    text = sh.read_text()
    assert 'python -m models.session_cost "$@"' in text and "MAD_HARNESS_CALLER_PWD" in text
