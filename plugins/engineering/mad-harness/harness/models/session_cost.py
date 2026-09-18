"""What one session cost in context — read from its transcript, so the next cost analysis
is a script and not $69 of hand work.

MEASURED. A field campaign orchestrator (strategic tier, 0.9.6) ran 237 requests with its
context growing 55k → 920k tokens, averaging ~380k. At that size every tool call it makes
re-reads ~$0.11–0.17 of context, about 6x what the same call costs a worker. What filled
it: 35% its own outputs (a response persists in the context of every later request), 35%
injected text (a subagent's result landing in full and then again as a task-notification,
skill loads, hook output), 7% tool results. So the levers at the top of the tree are fewer
turns — deterministic steps as scripts, one call in place of five to ten — and artefacts
passed by PATH rather than by content. The analysis that established this was 74 requests
of orchestrator time, $69, done by hand from the same transcript rows; this is that method
as a script, for the next time. Source: `.harness/cost_control_orchestration.md` in the
consuming project.

TOKENS, NOT DOLLARS. Prices differ per model and per cache tier, so a dollar figure
computed here is wrong the first time the tier changes; `models-cost.sh` reads the cost the
SDK reports per dispatch, which is the figure to trust. A request's context is
`cache_read + cache_creation + input` of its usage — what the model read to answer it.

    harness/checks/session-cost.sh <transcript.jsonl | session-id> [--json] [--top N]
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .transcript import _text, _when, projects_dir, result_volume, session_file

#: A context step this large between consecutive requests is a load — a skill, a hook's
#: output, a subagent's result — and the field analysis found each by hand. 15k is below
#: every load it named (26k, 350k) and above what one ordinary turn adds.
JUMP_TOKENS = 15_000
#: How many of the largest injected texts and tool results the report names.
TOP = 5
#: What is shown of a text or a command, whitespace collapsed.
HEAD = 70
#: The tool-input keys that say what a call did, in the order a reader wants them.
_WHAT_KEYS = ("command", "file_path", "path", "description", "pattern", "url", "prompt", "query", "skill")
#: The two CLI shapes whose first 70 characters are all prefix: a skill load opens with
#: the skill's directory, a task-notification with ids. Each is named by what it carries.
_SKILL = re.compile(r"^Base directory for this skill: (\S+)")
_NOTIFY = re.compile(r"^\s*<task-notification>.*?<summary>(.*?)</summary>", re.DOTALL)


@dataclass(frozen=True)
class Request:
    index: int
    at: float | None
    context: int
    output: int


@dataclass(frozen=True)
class Injected:
    #: The first request whose context carried it.
    request: int
    tokens: int
    meta: bool
    head: str


@dataclass(frozen=True)
class ToolResult:
    request: int
    tokens: int
    tool: str
    head: str


@dataclass(frozen=True)
class Jump:
    request: int
    at: float | None
    #: Signed: a positive jump is a load, a negative one a compaction or a context edit.
    size: int
    context: int
    #: The largest thing that landed between this request and the previous one: an
    #: injected text, a tool result, or the previous request's own output.
    landed: dict[str, Any] | None


@dataclass(frozen=True)
class SessionCost:
    path: Path
    requests: tuple[Request, ...]
    injected: tuple[Injected, ...]
    results: tuple[ToolResult, ...]
    #: Tool-result totals and cache breaks, from the one reader of those figures.
    volume: Any

    @property
    def contexts(self) -> list[int]:
        return [r.context for r in self.requests]

    @property
    def output_tokens(self) -> int:
        return sum(r.output for r in self.requests)

    @property
    def injected_tokens(self) -> int:
        return sum(i.tokens for i in self.injected)

    @property
    def tool_result_tokens(self) -> int:
        return self.volume.chars // 4

    def jumps(self, threshold: int = JUMP_TOKENS) -> list[Jump]:
        out: list[Jump] = []
        for prev, cur in zip(self.requests, self.requests[1:]):
            size = cur.context - prev.context
            if abs(size) > threshold:
                out.append(Jump(cur.index, cur.at, size, cur.context, self._landed(cur.index, prev)))
        return out

    def _landed(self, index: int, prev: Request) -> dict[str, Any] | None:
        """The biggest candidate for a step at `index`: an injected text or a tool result
        that arrived after the previous request, or that request's own output."""
        best: dict[str, Any] = {"kind": "output", "tokens": prev.output, "head": f"of request {prev.index}"}
        for i in self.injected:
            if i.request == index and i.tokens > best["tokens"]:
                best = {"kind": "injected", "tokens": i.tokens, "meta": i.meta, "head": i.head}
        for r in self.results:
            if r.request == index and r.tokens > best["tokens"]:
                best = {"kind": "result", "tokens": r.tokens, "tool": r.tool, "head": r.head}
        return best if best["tokens"] else None

    def summary(self, top: int = TOP, threshold: int = JUMP_TOKENS) -> dict[str, Any]:
        """The report as data — what `--json` prints and what the text is rendered from."""
        ctx = self.contexts
        n = len(ctx)
        last = ctx[-1] if ctx else 0
        share = lambda v: round(100 * v / last) if last else None  # noqa: E731
        return {
            "transcript": str(self.path),
            "requests": n,
            "first_at": _iso(self.requests[0].at) if n else None,
            "last_at": _iso(self.requests[-1].at) if n else None,
            "context": {
                "first": ctx[0] if ctx else 0,
                "last": last,
                "average": sum(ctx) // n if n else 0,
                "max": max(ctx) if ctx else 0,
            },
            "output_tokens": self.output_tokens,
            "injected_tokens": self.injected_tokens,
            "tool_result_tokens": self.tool_result_tokens,
            "share_of_last_context_pct": {
                "output": share(self.output_tokens),
                "injected": share(self.injected_tokens),
                "tool_results": share(self.tool_result_tokens),
            },
            "cache_breaks": dict(self.volume.cache_breaks),
            "rewritten_tokens": self.volume.rewritten_tokens,
            "jump_threshold": threshold,
            "jumps": [
                {"request": j.request, "at": _iso(j.at), "size": j.size, "context": j.context, "landed": j.landed}
                for j in self.jumps(threshold)
            ],
            "top_injected": [
                {"request": i.request, "tokens": i.tokens, "meta": i.meta, "head": i.head}
                for i in sorted(self.injected, key=lambda i: -i.tokens)[:top]
            ],
            "top_results": [
                {"request": r.request, "tokens": r.tokens, "tool": r.tool, "head": r.head}
                for r in sorted(self.results, key=lambda r: -r.tokens)[:top]
            ],
        }


def _iso(at: float | None) -> str | None:
    return datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if at is not None else None


def _head(text: str, n: int = HEAD) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _label(text: str) -> str:
    """The first 70 characters of an injected text, or its name when it is a skill load
    or a task-notification — the field session's two largest loads both opened with a
    path, and 70 characters of a path names nothing."""
    m = _SKILL.match(text)
    if m:
        return _head(f"skill {m.group(1).rstrip('/').rsplit('/', 1)[-1]}: {text[m.end():]}")
    m = _NOTIFY.match(text)
    if m:
        return _head(f"task-notification: {m.group(1)}")
    return _head(text)


def _what(inp: Any) -> str:
    """The command or path a tool call named, from its input."""
    if isinstance(inp, dict):
        for k in _WHAT_KEYS:
            v = inp.get(k)
            if isinstance(v, str) and v.strip():
                return v
        return json.dumps(inp, sort_keys=True) if inp else ""
    return "" if inp is None else str(inp)


def read(path: Path) -> SessionCost:
    """One pass for the request series, the injected texts and each tool result; the
    tool-result totals and cache breaks come from `transcript.result_volume`, which
    already reads them for every dispatch.

    A request is one API call. The CLI writes one row per content block, all sharing a
    `requestId`, and can write the same message again as streaming progresses — so rows
    are deduplicated by request and the LAST row's usage is the one kept."""
    requests: list[Request] = []
    at_index: dict[str, int] = {}
    injected: list[Injected] = []
    results: list[ToolResult] = []
    calls: dict[str, tuple[str, str]] = {}
    with path.open(errors="replace") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            # A sidechain row is a subagent's, written into its parent's file by older
            # CLIs; its requests are not this session's context.
            if row.get("isSidechain"):
                continue
            msg = row.get("message") or {}
            content = msg.get("content")
            if row.get("type") == "assistant":
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "tool_use":
                            calls[str(c.get("id"))] = (str(c.get("name") or "?"), _what(c.get("input")))
                rid = str(row.get("requestId") or msg.get("id") or f"row-{len(requests)}-{id(row)}")
                usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
                context = (
                    int(usage.get("cache_read_input_tokens") or 0)
                    + int(usage.get("cache_creation_input_tokens") or 0)
                    + int(usage.get("input_tokens") or 0)
                )
                # A row that read nothing is not a request the model answered — a rate
                # limit, an expired login — and counted, it is a 900k drop and a 900k jump.
                if context == 0:
                    continue
                if rid not in at_index:
                    at_index[rid] = len(requests)
                    requests.append(Request(len(requests) + 1, _when(row), 0, 0))
                k = at_index[rid]
                requests[k] = Request(requests[k].index, requests[k].at or _when(row), context, int(usage.get("output_tokens") or 0))
                continue
            if row.get("type") != "user":
                continue
            landing = len(requests) + 1
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = "".join(
                    str(c.get("text") or "") for c in content if isinstance(c, dict) and c.get("type") == "text"
                )
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "tool_result":
                        tool, what = calls.get(str(c.get("tool_use_id")), ("?", ""))
                        results.append(ToolResult(landing, len(_text(c.get("content"))) // 4, tool, _head(what)))
            else:
                text = ""
            if text:
                injected.append(Injected(landing, len(text) // 4, bool(row.get("isMeta")), _label(text)))
    return SessionCost(path, tuple(requests), tuple(injected), tuple(results), result_volume(path))


def render(cost: SessionCost, top: int = TOP, threshold: int = JUMP_TOKENS) -> str:
    s = cost.summary(top, threshold)
    ctx, pct = s["context"], s["share_of_last_context_pct"]
    show = lambda p: f" ({p}%)" if p is not None else ""  # noqa: E731
    day = lambda iso: iso[:16].replace("T", " ") + "Z" if iso else "—"  # noqa: E731
    when = f"{day(s['first_at'])} → {day(s['last_at'])}"
    lines = [
        f"session-cost — {s['transcript']}",
        f"{s['requests']} requests · {when}",
        "",
        f"{'context (tokens)':<22}first {ctx['first']:>9,}   last {ctx['last']:>9,}   average {ctx['average']:>9,}   max {ctx['max']:>9,}",
        f"{'persisting in it':<22}output {s['output_tokens']:,}{show(pct['output'])}   "
        f"injected {s['injected_tokens']:,}{show(pct['injected'])}   "
        f"tool results {s['tool_result_tokens']:,}{show(pct['tool_results'])}",
    ]
    breaks = s["cache_breaks"]
    if breaks:
        reasons = ", ".join(f"{k} {v}" for k, v in sorted(breaks.items()))
        lines.append(f"{'cache breaks':<22}{sum(breaks.values())} ({reasons}) · {s['rewritten_tokens']:,} tokens re-written")
    lines += ["", f"context jumps over {threshold:,} tokens between consecutive requests"]
    if s["jumps"]:
        lines.append(f"{'req':>6}  {'time':<12} {'size':>9}  {'context':>9}  landed")
        for j in s["jumps"]:
            lines.append(f"{j['request']:>6}  {_clock_iso(j['at']):<12} {j['size']:>+9,}  {j['context']:>9,}  {_landed_text(j['landed'])}")
    else:
        lines.append("  none")
    lines += ["", f"top {top} injected texts", f"{'req':>6} {'tokens':>9}  {'meta':<5} text"]
    for i in s["top_injected"]:
        lines.append(f"{i['request']:>6} {i['tokens']:>9,}  {'yes' if i['meta'] else 'no':<5} {i['head']}")
    lines += ["", f"top {top} tool results", f"{'req':>6} {'tokens':>9}  {'tool':<10} what"]
    for r in s["top_results"]:
        lines.append(f"{r['request']:>6} {r['tokens']:>9,}  {r['tool'][:10]:<10} {r['head']}")
    lines += [
        "",
        "Tokens, not dollars: prices differ per model and per cache tier; models-cost.sh has the "
        "SDK's per-dispatch cost. Shares are of the last request's context, so after a compaction "
        "they can pass 100%.",
        "output = the model's own responses, each persisting in the context of every later request. "
        "injected = what skills, hooks, task-notifications and the operator put in a user turn "
        "(meta = the CLI put it there, not a person). tool results = what the model's own calls "
        "returned, chars ÷ 4. A jump is a load, re-read on every request after it; a drop is a "
        "compaction or a context edit. A cache break re-wrote the whole prefix at the write rate.",
    ]
    return "\n".join(lines)


def _landed_text(landed: dict[str, Any] | None) -> str:
    if not landed:
        return "—"
    tag = " (meta)" if landed.get("meta") else f" {landed['tool']}" if landed.get("tool") else ""
    return f"{landed['kind']} {landed['tokens']:,}{tag} {landed['head']}"


def _clock_iso(iso: str | None) -> str:
    """`MM-DD HH:MMZ` — an orchestrator session spans days, so the day is part of the time."""
    return iso[5:16].replace("T", " ") + "Z" if iso else "—"


def resolve_transcript(arg: str, cwd: str | Path | None = None) -> Path | None:
    """A path to a transcript, or a session id looked up the way the CLI files it."""
    p = Path(arg)
    if p.is_file():
        return p
    return session_file(cwd or os.environ.get("MAD_HARNESS_CALLER_PWD") or os.getcwd(), arg)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="session-cost.sh",
        description="What one session cost in context, from its transcript: requests, context growth, what filled it.",
    )
    ap.add_argument("transcript", help="a session transcript (.jsonl), or a session id to find under the CLI's projects directory")
    ap.add_argument("--json", action="store_true", help="the report as JSON")
    ap.add_argument("--top", type=int, default=TOP, help=f"how many injected texts and tool results to name (default {TOP})")
    ap.add_argument("--jump", type=int, default=JUMP_TOKENS, help=f"a context step this large is a jump (default {JUMP_TOKENS:,})")
    args = ap.parse_args(argv)

    path = resolve_transcript(args.transcript)
    if path is None:
        print(f"no transcript at {args.transcript!r} and no session of that id under {projects_dir()}", file=sys.stderr)
        return 2
    cost = read(path)
    if not cost.requests:
        print(f"{path}: no requests — nothing to measure", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(cost.summary(args.top, args.jump), indent=2))
    else:
        print(render(cost, args.top, args.jump))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
