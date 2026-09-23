"""Validate that a provider is actually usable before any real task is routed to it.

WHY THIS GATES PHASE C. "Anthropic-compatible" is a claim about an API surface,
not a guarantee about the parts an agent harness leans on. Claude Code drives
multi-turn tool loops; a provider can return perfect single-shot completions and
still fail to sustain one. That failure does not look like an error — it looks
like a worker that read no files and confidently produced nothing, which the
lenses then judge as bad work rather than as a broken provider.

So each probe below is a thing that has to work for a WORKER to work, ordered so
the cheapest and most fundamental fails first. Anything unproven is reported as
UNPROVEN rather than assumed; the exit code is non-zero unless every probe passes.

    harness/models/probe-compat.sh deepseek
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .resolve import ConfigError, load_config, provider_env


@dataclass
class Probe:
    name: str
    why: str
    ok: bool | None  # None = unproven
    detail: str


def _run(
    env_overlay: dict[str, str],
    model: str,
    prompt: str,
    cwd: Path,
    tools: list[str] | None = None,
    timeout: int = 180,
) -> dict:
    import os

    # A NESTED SESSION'S OWN VARIABLES ARE NOT THE PROBE'S. `CLAUDECODE`, the messaging
    # socket and the session ids are set in every Claude Code session, and an operator runs
    # this probe from one; inherited, the child refuses to start ("another auth source is
    # set and takes precedence") and the probe reports the PROVIDER as unreachable — which
    # is the one conclusion it must never reach wrongly. Same reasoning as
    # `dispatch.STRIPPED_FROM_CHILDREN`, for the same class of defect.
    env = {k: v for k, v in os.environ.items() if not (k == "CLAUDECODE" or k.startswith("CLAUDE_CODE_"))}
    env.pop("VIRTUAL_ENV", None)
    env.update(env_overlay)
    cmd = [
        "claude",
        "-p",
        "--model",
        model,
        "--max-budget-usd",
        "0.50",
        "--output-format",
        "json",
        # THE SCRATCH DIRECTORY IS THE PROBE'S OWN, and the tool probes must be able to use
        # it. Without a mode, a headless run has no one to approve `Write`, so the
        # multi-turn probe ended "every method of creating step1.txt requires a permission
        # approval that hasn't been granted" — reported as the PROVIDER failing a tool loop
        # it had in fact driven for nine turns. `acceptEdits` is the narrowest mode that
        # lets a file be written; the directory is a fresh mkdtemp thrown away after.
        "--permission-mode",
        "acceptEdits",
    ]
    # `--tools=<value>`, ATTACHED, ONE COMMA-SEPARATED VALUE. The flag is declared
    # `--tools <tools...>`: variadic, so a SEPARATE argument makes it consume everything
    # after it, including the prompt, which the CLI then reports as "Input must be provided
    # either through stdin or as a prompt argument" — and the probe blamed the PROVIDER,
    # the one conclusion it must never reach wrongly. Spelled as separate words
    # (`"--tools", "Read", "Bash"`) it ate the prompt as a third tool; joined but detached
    # (`"--tools", "Read,Bash"`) it ate the prompt as a second. Only the attached form
    # binds exactly one value. Measured against DeepSeek, which read a file and answered
    # in two turns by hand while the probe was calling it unreachable — so its two tool
    # probes, "the probe that matters" included, had never once run against any provider.
    if tools is not None:
        cmd.append(f"--tools={','.join(tools)}")
    cmd.append(prompt)
    proc = subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout
    )
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "result":
                return obj
    return {
        "_unparseable": True,
        "_stderr": proc.stderr[-500:],
        "_code": proc.returncode,
    }


def probe(provider: str) -> list[Probe]:
    config = load_config()
    env, missing = provider_env(provider, config)
    if missing:
        return [
            Probe(
                "credentials",
                "nothing can be probed without them",
                False,
                f"unset: {', '.join(missing)}. Copy harness/.env.example to "
                f"harness/.env and fill it in.",
            )
        ]

    # The model comes from a TIER that uses this provider, never from the
    # provider block — same rule the dispatcher follows, so the probe exercises
    # the real path rather than a parallel one that could pass while it fails.
    tiers = [t for t in config["tiers"].values() if t["provider"] == provider]
    if not tiers:
        return [
            Probe(
                "tier",
                "a provider is only reachable through a tier that uses it",
                False,
                f"no tier in tiers.yaml has provider: {provider}. Point one at it "
                f"first — e.g. worker: {{ provider: {provider}, model: <id> }}.",
            )
        ]
    model = tiers[0]["model"]

    probes: list[Probe] = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "canary.txt").write_text("the canary word is PERSIMMON\n")

        # 1. Reachability. Everything else is meaningless if this fails.
        r = _run(env, model, "Reply with exactly: PONG", work, tools=[])
        probes.append(
            Probe(
                "reachability",
                "the endpoint answers at all",
                not r.get("_unparseable") and not r.get("is_error"),
                r.get("result", "")[:120]
                or f"unparseable: {r.get('_stderr', '')[:200]}",
            )
        )
        if not probes[-1].ok:
            return probes

        # 2. Instruction following. A provider that ignores an exact-output
        #    instruction will also ignore a return contract.
        probes.append(
            Probe(
                "instruction following",
                "the ten-line return contract depends on it",
                (r.get("result") or "").strip().upper().startswith("PONG"),
                f"asked for exactly PONG, got {(r.get('result') or '')[:80]!r}",
            )
        )

        # 3. Token accounting. Without it the cost series is fiction and
        #    --max-budget-usd cannot enforce anything.
        usage = r.get("usage") or {}
        counted = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
        probes.append(
            Probe(
                "token accounting",
                "the budget ceiling and cost telemetry need it",
                counted > 0,
                f"input={usage.get('input_tokens')} output={usage.get('output_tokens')} "
                f"cost_usd={r.get('total_cost_usd')}",
            )
        )

        # 4. Single tool call. A worker that cannot read a file cannot work.
        r2 = _run(
            env,
            model,
            "Read the file canary.txt in the current directory and reply with "
            "ONLY the canary word it contains.",
            work,
            tools=["Read", "Bash"],
        )
        got = r2.get("result") or ""
        probes.append(
            Probe(
                "tool call (read)",
                "a worker that cannot read a file cannot work",
                "PERSIMMON" in got.upper(),
                f"got {got[:120]!r}",
            )
        )

        # 5. Multi-turn tool loop. THE probe that matters: this is where
        #    structurally-similar APIs most often diverge, and the failure is
        #    silent — it looks like a lazy worker, not a broken provider.
        r3 = _run(
            env,
            model,
            "Do this in steps using tools: create a file step1.txt containing "
            "the number 7, then read it back, then reply with ONLY that number "
            "multiplied by 6.",
            work,
            tools=["Read", "Write", "Bash"],
        )
        got3 = r3.get("result") or ""
        probes.append(
            Probe(
                "multi-turn tool loop",
                "the failure mode is a worker that silently does nothing",
                "42" in got3 and int(r3.get("num_turns") or 0) >= 1,
                f"turns={r3.get('num_turns')} got {got3[:120]!r}",
            )
        )

    return probes


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: probe-compat.sh <provider>", file=sys.stderr)
        return 2
    provider = args[0]

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    if provider not in (config.get("providers") or {}):
        print(
            f"FAIL: unknown provider {provider!r}; known: {sorted(config.get('providers') or {})}",
            file=sys.stderr,
        )
        return 2

    print(f"probing provider: {provider}\n")
    results = probe(provider)
    for p in results:
        mark = {True: "PASS", False: "FAIL", None: "UNPROVEN"}[p.ok]
        print(f"  [{mark:8s}] {p.name}")
        print(f"             why: {p.why}")
        print(f"             {p.detail}")

    failed = [p for p in results if p.ok is not True]
    if failed:
        print(
            f"\nFAIL — {len(failed)} of {len(results)} probes did not pass. "
            f"Do NOT route a task to {provider!r}.\n"
            f"A provider that fails the tool-loop probe produces workers that "
            f"appear lazy rather than broken, and the lenses will blame the work.",
            file=sys.stderr,
        )
        return 1
    print(f"\nOK — {provider} passed all {len(results)} probes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
