#!/usr/bin/env python3
"""PreToolUse hook on Bash: a harness wrapper called with an env-assignment prefix is the
wrapper, and is allowed as the wrapper is.

Permission rules match command TEXT, and text that starts with `VAR=x` or `env -u VAR`
is not the command — so `MAD_HARNESS_CALLER_PWD="$PWD" …/verify/run.sh …` matched no rule
and was denied, while `…/verify/run.sh …` is granted. Measured: the commonest denial shape
in the lab (~50 of 76), every one a worker prefixing a wrapper it was already allowed to
run. Under the sandbox the prefix changes nothing the boundary cares about: the wrapper
sets its own environment and the command is contained either way.

This hook allows exactly that spelling and nothing else: after the prefixes the command
must be one simple call to an executable under HARNESS_ROOT, with no operator, no
substitution, no pipe, no redirect, and no `git push` anywhere. It runs only in a
dispatched session (HARNESS_ROOT is set); elsewhere it says nothing. It is plain python3
rather than the uv wrapper because it runs on every Bash call a worker makes — a uv
start-up per call would cost more than the denials did.
"""

# The system python3 may be 3.9: no 3.10 syntax anywhere below.
from __future__ import annotations

import json
import os
import re
import shlex
import sys

PREFIX_ENV = re.compile(r"^(env(\s+-u\s+[A-Za-z_][A-Za-z0-9_]*)*(\s+[A-Za-z_][A-Za-z0-9_]*=\S*)*\s+)?((\s*[A-Za-z_][A-Za-z0-9_]*=\S*)\s+)*")
#: Unquoted, any of these makes it more than one simple call. Quoted, they are text —
#: `tk.sh note X "<1-3>"` is a note, not a redirect — and the lexer tells the two apart.
OPERATORS = {"|", "||", "&", "&&", ";", ";;", "(", ")", "<", ">", ">>", "<<", "<<<", "|&", "&>"}


def _simple_call(rest: str) -> list[str] | None:
    """argv if `rest` is one simple command — no operator, substitution or expansion
    outside quotes — else None."""
    if "`" in rest or "\n" in rest:
        return None
    lex = shlex.shlex(rest, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        tokens = list(lex)
    except ValueError:
        return None
    if not tokens or any(t in OPERATORS for t in tokens):
        return None
    if any(t.startswith("$") for t in tokens):
        return None
    return tokens


def decide(command: str, harness_root: str) -> dict | None:
    if not harness_root or not command:
        return None
    if "git push" in command or "$(" in command or "`" in command:
        return None  # a substitution anywhere, prefix included, is not a simple call
    m = PREFIX_ENV.match(command)
    rest = command[m.end():] if m else command
    if rest == command:
        return None  # no prefix — the rules decide, as they do today
    argv = _simple_call(rest)
    if not argv:
        return None
    exe = argv[0]
    root = os.path.realpath(harness_root)
    if not os.path.isabs(exe) or not os.path.realpath(exe).startswith(root + os.sep):
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": (
                f"an env-assignment prefix on a harness wrapper: {os.path.relpath(exe, root)} is granted, "
                f"the prefix changes nothing the sandbox cares about"
            ),
        }
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0
    command = str((payload.get("tool_input") or {}).get("command") or "")
    verdict = decide(command, os.environ.get("HARNESS_ROOT", ""))
    if verdict:
        print(json.dumps(verdict))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
