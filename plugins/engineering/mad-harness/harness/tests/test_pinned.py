"""The pinned state a compaction cannot lose, because nothing in it comes from memory.

Measured: a campaign session's compaction summary kept 2 of 9 recent task ids and dropped
two mid-dispatch. Workers never compact (0 of 33 transcripts); the orchestrator does.
"""

from __future__ import annotations

import json

from models import pinned as mod


def _state(claims=(), worktrees=(), slot=None):
    return {
        "claims": [
            {"task": t, "holder": "campaign", "host": "mac", "age_s": 600, "alive": True, "stale": False}
            for t in claims
        ],
        "worktrees": [(t, f"/repo/.claude/worktrees/harness-w1-{t}") for t in worktrees],
        "slot": slot,
    }


def test_the_invariants_come_from_the_loop_skill_and_name_its_hard_rules():
    rules = mod.invariants()
    assert "decision" in rules and "resume-point.sh" in rules and "lens gate" in rules
    assert "${CLAUDE_PLUGIN_ROOT}" not in mod.render(_state(), source="compact").split("## The rules")[0]


def test_the_render_names_every_pinned_id_the_summary_dropped():
    state = _state(claims=("PROJ-a1", "PROJ-b2"), worktrees=("PROJ-b2", "PROJ-c3"), slot="campaign")
    assert mod.pinned_ids(state) == ["PROJ-a1", "PROJ-b2", "PROJ-c3"]
    out = mod.render(state, summary="we were working PROJ-b2 and had merged something", source="compact")
    assert "DROPPED 2 pinned id(s): PROJ-a1, PROJ-c3" in out
    assert "`PROJ-a1` held by campaign" in out and "harness-w1-PROJ-c3" in out and "- campaign" in out
    assert "kept every pinned id" in mod.render(state, summary="PROJ-a1 PROJ-b2 PROJ-c3")
    assert "summary" not in mod.render(state).lower().split("## claims")[0]


def test_the_last_compaction_summary_is_read_from_the_transcript(tmp_path):
    p = tmp_path / "t.jsonl"
    rows = [
        {"type": "user", "message": {"content": "hello"}},
        {"type": "user", "isCompactSummary": True, "message": {"content": [{"type": "text", "text": "first summary PROJ-a1"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}},
        {"type": "user", "isCompactSummary": True, "message": {"content": "second summary, no ids"}},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert mod.last_compact_summary(p) == "second summary, no ids"
    assert mod.last_compact_summary(tmp_path / "missing.jsonl") is None
    assert mod.last_compact_summary(None) is None


def test_harness_worktrees_are_recognised_by_both_naming_schemes_and_nothing_else(tmp_path, monkeypatch):
    porcelain = "\n".join([
        "worktree /repo", "HEAD abc", "branch refs/heads/main", "",
        "worktree /repo/.claude/worktrees/harness-w2-PROJ-9x", "HEAD def", "branch refs/heads/harness/PROJ-9x", "",
        "worktree /repo/.claude/worktrees/worktree-agent-PROJ-old", "HEAD 123", "",
        "worktree /elsewhere/plugin-fb992b5c1022", "HEAD 456", "",
    ])
    import subprocess

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, porcelain, ""))
    assert mod.worktrees(tmp_path) == [
        ("PROJ-9x", "/repo/.claude/worktrees/harness-w2-PROJ-9x"),
        ("PROJ-old", "/repo/.claude/worktrees/worktree-agent-PROJ-old"),
    ]


def test_the_hook_is_silent_when_nothing_is_in_flight_and_loud_when_something_is(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(mod, "in_flight", lambda: _state())
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps({"source": "compact"})))
    assert mod.main(["--hook"]) == 0 and capsys.readouterr().out == ""

    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"type": "user", "isCompactSummary": True, "message": {"content": "summary mentions PROJ-b2 only"}}) + "\n")
    monkeypatch.setattr(mod, "in_flight", lambda: _state(claims=("PROJ-a1", "PROJ-b2")))
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps({"source": "compact", "transcript_path": str(t)})))
    assert mod.main(["--hook"]) == 0
    out = capsys.readouterr().out
    assert "Injected on `compact`" in out and "DROPPED 1 pinned id(s): PROJ-a1" in out
    # On a plain startup there is no summary to check, only the state to show.
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps({"source": "startup", "transcript_path": str(t)})))
    assert mod.main(["--hook"]) == 0
    out = capsys.readouterr().out
    assert "Injected on `startup`" in out and "DROPPED" not in out and "kept every" not in out


def test_the_plugin_hook_points_at_the_wrapper_and_fires_on_compaction():
    from models.resolve import PLUGIN_ROOT

    spec = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text())
    [entry] = spec["hooks"]["SessionStart"]
    assert "compact" in entry["matcher"] and "resume" in entry["matcher"]
    [hook] = entry["hooks"]
    assert hook["command"].startswith('"${CLAUDE_PLUGIN_ROOT}/harness/swarm/pinned.sh"')
    assert (PLUGIN_ROOT / "harness" / "swarm" / "pinned.sh").exists()
