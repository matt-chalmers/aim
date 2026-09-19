"""The permission broker: what happens when a dispatched agent is refused a tool.

WHY THIS EXISTS. Headless dispatch has no approver, so a permission an agent does not
carry is simply denied — and the agent is told nothing useful, carries on, and returns a
confident answer built on work it could not do. Measured repeatedly in this repo: a lens
that could not read the brief written for it still returned VERDICT: PASS; a worker denied
its test command still reported the suite green. The denial is recorded in telemetry
afterwards, which is too late to change what the agent did.

WHAT IT DOES NOT DO: widen anything. Every request that reaches the broker is one the
grants did not cover, and it is still refused — the grants stay a ceiling, which is the
property the last round of fixes was about. What changes is that the refusal now carries a
REASON THE AGENT CAN ACT ON, and lands in the run directory where a human can read it.

The remedies below are not general advice. Each is a failure this harness actually shipped,
and each cost a wave.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from tracker.port import DECISION, PERMISSION_PREFIX

#: Denials whose cause is known, and the remedy that actually works. Ordered: the first
#: match wins, so the most specific pattern comes first.
KNOWN: tuple[tuple[str, str], ...] = (
    (
        r"source\s+\.swarm-env",
        "Do not source .swarm-env. Run the command through harness/verify/run.sh, which "
        "loads it for you. Sourcing cannot work regardless: every Bash call is a fresh "
        "shell, so the exports would not reach your next command.",
    ),
    (
        r"^\s*\w+=[^\s]+\s+\S",
        "A command that starts with VAR=value matches no permission rule, because rules "
        "match the text before the shell expands anything. Run it through "
        "harness/verify/run.sh, which supplies the environment.",
    ),
    (
        r"git\s+push",
        "A worker commits; it never pushes. The orchestrator integrates the wave. This is "
        "not a missing grant — it is the contract.",
    ),
    (
        # `env` and `printenv` are the obvious spellings; `echo $VAR` and
        # `printf '%s' "${VAR}"` are the same question asked sideways, and one of those
        # reached the operator queue in a live wave before this pattern existed.
        # `[\s\S]*` rather than `.*`: the command that reached the operator queue had a
        # literal newline inside its format string, and `.` does not cross one.
        r"^\s*(env|printenv|set)\b|^\s*(echo|printf)\b[\s\S]*\$\{?[A-Z_]{3,}",
        "Do not inspect the environment: it carries provider credentials that must never "
        "reach an agent's output. The harness has already set it correctly, and the three "
        "directories you might be looking for are named under 'Where you are'.",
    ),
    (
        r"&&|;|\|",
        "A compound command matches no permission rule even when every part of it is "
        "granted separately. Issue the parts as separate calls.",
    ),
    (
        r"\$\{?[A-Z_]+\}?/",
        "A command written with a shell variable cannot be granted: rules match the text "
        "before expansion. Substitute the absolute path you were given.",
    ),
)


def explain(command: str) -> str:
    """The remedy for a denied command, or a truthful admission that there is none."""
    for pattern, remedy in KNOWN:
        if re.search(pattern, command):
            return remedy
    return (
        "This command is not covered by the grants for your agent. Do not work around it "
        "and do not pretend the step succeeded: report BLOCKED, naming this command."
    )


def _command_of(tool_name: str, tool_input: dict[str, Any]) -> str:
    if tool_name == "Bash":
        return str(tool_input.get("command") or "")
    return str(tool_input.get("file_path") or tool_input.get("path") or "")


#: Denials worth asking an operator about. A remedy the agent can act on is not a
#: permission problem — it is a malformed command, and filing a request for it would fill
#: the queue with noise until nobody reads it. Only a refusal with NO remedy is a question.
def _is_answerable(remedy: str) -> bool:
    return remedy.startswith("This command is not covered")


def file_request(agent: str, task: str | None, tool: str, command: str) -> str | None:
    """File a `permission` record for an operator to answer. Returns its id, or None.

    ASYNCHRONOUS BY CONSTRUCTION. The worker does not wait — nobody is watching an
    unattended campaign, and a dispatch blocked on a human would hold the whole wave open.
    It files the question, reports BLOCKED, and the answer applies to the NEXT dispatch.
    That is the same shape `decision` records already use, and the reason this reuses them
    rather than inventing scheduling.

    THE RAW COMMAND IS THE RECORD. An agent's account of why it needs something is
    model-written text, and a prompt-injected agent would write a persuasive one, so the
    operator is shown what would actually run rather than a summary of it.

    ONE REQUEST PER COMMAND. A wave of eight workers meeting the same wall would otherwise
    file eight identical questions.
    """
    try:
        import tracker

        store = tracker.task_store()
        digest = hashlib.sha256(command.encode()).hexdigest()[:12]
        marker = f"[permission:{digest}]"

        for existing in store.list(type=DECISION):
            if marker in (existing.description or "") and existing.is_open:
                _block(store, task, existing.id)
                return existing.id

        request = store.create(
            f"{PERMISSION_PREFIX} {tool} — {command.splitlines()[0][:60]}",
            type=DECISION,
            description=(
                f"{marker}\n\n"
                f"An agent was refused a tool and could not proceed.\n\n"
                f"AGENT: {agent}\nTASK: {task or '(none)'}\nTOOL: {tool}\n\n"
                f"THE COMMAND, VERBATIM — this is what would run:\n\n"
                f"```\n{command[:2000]}\n```\n\n"
                f"TO ANSWER: add the narrowest rule that unblocks this to `permissions.allow`\n"
                f"in harness.yaml and close this record, or close it with a reason and the\n"
                f"worker will be told to take another route. A grant here can never defeat a\n"
                f"deny rule.\n"
            ),
        )
        _block(store, task, request)
        return request
    except Exception:  # noqa: BLE001 — filing must never fail a dispatch
        return None


def _block(store: Any, task: str | None, request: str) -> None:
    """Stop the scheduler offering a task whose answer has not arrived.

    WITHOUT THIS IT IS A SPIN, NOT A LOOP. The task stays ready, is dispatched again next
    wave, meets the same wall, and the request is deduplicated back to the same record —
    burning a dispatch per wave to re-ask a question already asked. The edge is what makes
    the campaign skip it and pick it up again once a person answers, which is exactly how
    `decision` records already gate the work behind them.
    """
    if not task:
        return
    try:
        store.dep_add(task, request)
    except Exception:  # noqa: BLE001 — a missing edge must not fail the dispatch
        pass


def broker(agent: str, task: str | None = None, sink: Path | None = None):
    """A `can_use_tool` callback that refuses with a reason and records the request.

    :param sink: where to append the record. Defaults to the run directory, so a denial
        lands beside the dispatch telemetry for the same wave.
    """

    async def can_use_tool(tool_name: str, tool_input: dict[str, Any], context: Any):
        from claude_agent_sdk import PermissionResultDeny

        command = _command_of(tool_name, tool_input)
        remedy = explain(command)
        request = file_request(agent, task, tool_name, command) if _is_answerable(remedy) else None
        _append(
            sink,
            {
                "agent": agent,
                "task": task,
                "tool": tool_name,
                "command": command[:600],
                # The CLI's own diagnosis when it has one ("cannot be statically
                # analyzed", "requires approval"); when it has none the denial is the
                # plain case, and a record should say so rather than carry a null.
                "reason": getattr(context, "decision_reason", None) or "no rule matched",
                "remedy": remedy,
                "request": request,
            },
        )
        asked = (
            f"\n\nAn operator has been asked about this, as {request}. You cannot wait for "
            f"the answer — nobody is watching. Take another route if one exists, or report "
            f"BLOCKED naming this command."
            if request
            else ""
        )
        return PermissionResultDeny(
            message=f"DENIED: {tool_name} was not permitted for this dispatch.\n{remedy}{asked}"
        )

    return can_use_tool


def _append(sink: Path | None, row: dict[str, Any]) -> None:
    """Recording must never fail a dispatch, exactly as telemetry must not."""
    try:
        if sink is None:
            from tracker.locks import run_dir

            sink = run_dir() / "events" / "harness.denied.jsonl"
        sink.parent.mkdir(parents=True, exist_ok=True)
        with sink.open("a") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        pass


def resolved_requests(task: str | None) -> str:
    """What an operator already decided about this task's permission requests.

    A RE-DISPATCHED WORKER IS A FRESH AGENT WITH NO MEMORY OF THE FIRST ATTEMPT. Without
    this it rediscovers the blocked approach, files the same request — deduplicated back to
    the record just answered — and the loop spins instead of advancing. A refusal is worth
    carrying most of all: "this was refused, because X" is what makes the worker choose a
    different route rather than the same one.
    """
    if not task:
        return ""
    try:
        import tracker

        store = tracker.task_store()
        answered = [
            t
            for t in store.list(type=DECISION)
            if t.title.startswith(PERMISSION_PREFIX)
            and not t.is_open
            and f"TASK: {task}" in (t.description or "")
        ]
        if not answered:
            return ""
        lines = [
            "## Permission requests already answered for this task\n",
            "An operator has ruled on these. Do not ask again — a repeat request is "
            "deduplicated onto the record that was just closed, which stalls you.\n",
        ]
        for t in answered:
            lines.append(f"- **{t.title}**\n  Operator's answer: {t.close_reason or '(no reason given)'}")
        lines.append(
            "\nIf an answer approved something, it is already in your grants. If it refused, "
            "take the route the reason points at, or report BLOCKED — do not retry it."
        )
        return "\n".join(lines) + "\n"
    except Exception:  # noqa: BLE001 — never fail a dispatch over context
        return ""
