"""The Agent-tool guard: this plugin's agents run through dispatch.sh, and the hook says
so with the form to use. Measured: 67.2M tokens went through the Agent tool unrecorded
and uncapped in five field sessions, against 6.9M through the dispatcher."""

from __future__ import annotations

import io
import json
import subprocess

from models import guard_agent_tool as mod
from models.resolve import PLUGIN_ROOT, plugin_name


def _call(tool="Agent", **tool_input):
    return {"tool_name": tool, "tool_input": tool_input, "session_id": "s", "cwd": "/x"}


def test_a_plugin_agent_is_denied_with_the_dispatch_form_under_either_tool_name():
    name = plugin_name()
    for tool in ("Agent", "Task"):
        v = mod.decision(_call(tool, subagent_type=f"{name}:architect", prompt="design it"))
        out = v["hookSpecificOutput"]
        assert out["permissionDecision"] == "deny" and out["hookEventName"] == "PreToolUse"
        assert f"{PLUGIN_ROOT}/harness/models/dispatch.sh architect --prompt-file" in out["permissionDecisionReason"]
        assert "${CLAUDE_PLUGIN_ROOT}" not in out["permissionDecisionReason"]
    # The bare name too — the CLI accepts both spellings.
    assert mod.decision(_call(subagent_type="planner"))["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_everything_that_is_not_a_plugin_agent_passes_untouched():
    assert mod.decision(_call(subagent_type="Explore", prompt="find x")) is None
    assert mod.decision(_call(subagent_type="general-purpose")) is None
    assert mod.decision(_call(subagent_type="other-plugin:architect")) is None
    assert mod.decision(_call(subagent_type="")) is None
    assert mod.decision(_call()) is None
    assert mod.decision(_call("Bash", command="ls")) is None
    assert mod.decision({}) is None


def test_the_guard_can_fail_and_the_hook_is_registered(monkeypatch, capsys):
    """A guard that cannot fail is decoration: a payload for a plugin agent must produce a
    deny on stdout, a payload for anything else must produce nothing, and a broken payload
    must not break the tool call."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_call(subagent_type=f"{plugin_name()}:verifier"))))
    assert mod.main() == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_call(subagent_type="Explore"))))
    assert mod.main() == 0 and capsys.readouterr().out == ""
    monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
    assert mod.main() == 0 and capsys.readouterr().out == ""

    spec = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text())
    [entry] = [e for e in spec["hooks"]["PreToolUse"] if e["matcher"] == "Agent|Task"]
    assert entry["matcher"] == "Agent|Task"
    assert entry["hooks"][0]["command"].startswith('"${CLAUDE_PLUGIN_ROOT}/harness/swarm/guard-agent-tool.sh"')
    # And the wrapper the hook names really denies, end to end.
    proc = subprocess.run(
        [str(PLUGIN_ROOT / "harness" / "swarm" / "guard-agent-tool.sh")],
        input=json.dumps(_call(subagent_type=f"{plugin_name()}:analyst")),
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0 and '"deny"' in proc.stdout


def test_agent_teams_are_not_an_exemption(monkeypatch):
    """0.10.31 built a /design-debate that ran architect and analyst as teammates, measured
    it (~$33 against /design's ~$14, no measurable difference, zero dispatch events for $21
    of it) and removed it. No environment makes a plugin agent pass this hook."""
    name = plugin_name()
    monkeypatch.setenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", "1")
    monkeypatch.setenv("MAD_HARNESS_TEAMS_DEBATE", "1")
    for agent in ("architect", "analyst", "planner", "verifier"):
        for payload in (_call(subagent_type=f"{name}:{agent}"), _call(subagent_type=f"{name}:{agent}", name="a-teammate")):
            v = mod.decision(payload)
            assert v is not None and v["hookSpecificOutput"]["permissionDecision"] == "deny", agent
