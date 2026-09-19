"""The prefix-tolerant allow: a harness wrapper with an env-assignment prefix is allowed
as the wrapper is; nothing else is touched. Measured: ~50 of 76 lab denials were this
shape, every one a worker prefixing a wrapper it was already granted."""

from __future__ import annotations

import importlib.util
import json
import subprocess

import pytest

from models.resolve import HARNESS, PLUGIN_ROOT

SCRIPT = HARNESS / "swarm" / "allow-prefixed-wrapper.py"
spec = importlib.util.spec_from_file_location("allow_prefixed_wrapper", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

TK = str(HARNESS / "tracker" / "tk.sh")
RUN = str(HARNESS / "verify" / "run.sh")


@pytest.mark.parametrize("command", [
    f'MAD_HARNESS_CALLER_PWD="$PWD" {RUN} --lane backend --scoped tests/test_x.py test_scoped',
    f"env -u VIRTUAL_ENV MAD_HARNESS_CALLER_PWD=/w {TK} show PROJ-1",
    f"env -u VIRTUAL_ENV {TK} note PROJ-1 'a note with <1-3> in it'",
    f"A=1 B=two {RUN} test",
])
def test_a_prefixed_wrapper_is_allowed_with_the_reason(command):
    v = mod.decide(command, str(HARNESS))
    assert v["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert "prefix" in v["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("command", [
    f"{TK} list",                                          # no prefix: the rules decide, as today
    "FOO=1 git push origin main",                          # never
    f"X=1 {RUN} test 2>&1 | tail -20",                     # a pipe: not one simple call
    f"X=1 {RUN} test && echo done",                        # an operator
    f"MAD_HARNESS_CALLER_PWD=$(pwd) {RUN} test",           # a substitution
    f"env -u VIRTUAL_ENV uv run --directory {HARNESS} python -m models.commands",  # not a wrapper
    "VAR=1 /usr/bin/env python3 -c 'print(1)'",            # outside the harness
    f"X=1 {RUN} $ARG",                                     # an expanded argument
    f"X=1 ../../{HARNESS.name}/verify/run.sh test",        # relative: not provably the wrapper
])
def test_everything_else_is_left_to_the_rules(command):
    assert mod.decide(command, str(HARNESS)) is None


def test_silent_outside_a_dispatched_session_and_on_a_broken_payload(capsys, monkeypatch):
    assert mod.decide(f"X=1 {RUN} test", "") is None
    monkeypatch.delenv("HARNESS_ROOT", raising=False)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("{not json"))
    assert mod.main() == 0 and capsys.readouterr().out == ""


def test_the_hook_is_registered_and_the_script_runs_end_to_end():
    spec = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text())
    entries = {e["matcher"]: e for e in spec["hooks"]["PreToolUse"]}
    assert "Bash" in entries
    assert entries["Bash"]["hooks"][0]["command"].startswith('"${CLAUDE_PLUGIN_ROOT}/harness/swarm/allow-prefixed-wrapper.py"')
    payload = {"tool_name": "Bash", "tool_input": {"command": f"MAD_HARNESS_CALLER_PWD=/w {TK} show PROJ-1"}}
    proc = subprocess.run([str(SCRIPT)], input=json.dumps(payload), capture_output=True, text=True, timeout=30,
                          env={"PATH": "/usr/bin:/bin", "HARNESS_ROOT": str(HARNESS)})
    assert proc.returncode == 0 and '"allow"' in proc.stdout
    proc = subprocess.run([str(SCRIPT)], input=json.dumps(payload), capture_output=True, text=True, timeout=30,
                          env={"PATH": "/usr/bin:/bin"})
    assert proc.returncode == 0 and proc.stdout == ""
