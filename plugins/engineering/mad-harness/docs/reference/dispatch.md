# Dispatch

One agent invocation: config resolution, containment, execution, telemetry.
`harness/models/dispatch.py`, `harness/models/resolve.py`.

## Data flow

<img src="../assets/dispatch-flow.svg" alt="Dispatch: resolution, containment, execution and telemetry">


## Types

```python
@dataclass(frozen=True)
class Resolved:
    agent: str; tier: str; reason: str          # why this tier won
    provider: str; model: str; effort: str
    max_budget_usd: float
    env: dict[str, str]                          # provider credentials
    missing_env: tuple[str, ...]                 # unset -> refuse to dispatch
    permission_mode: str = "default"             # or "acceptEdits"
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    add_dirs: tuple[str, ...] = ()               # readable outside cwd
    setting_sources: str = "project"
    sandbox: dict[str, Any] = field(default_factory=dict)
    settings: str = ""                           # JSON: sandbox.filesystem
    plugin_dir: str | None = None

@dataclass(frozen=True)
class Outcome:
    resolved: Resolved
    ok: bool                                     # see "ok is not success"
    text: str
    cost_usd: float                              # client-side estimate
    input_tokens: int; output_tokens: int
    cache_read_tokens: int; cache_creation_tokens: int
    turns: int; duration_ms: int; session_id: str
    permission_denials: list[Any]
    raw: dict[str, Any]                          # diagnostics only
    # derived: terminal ("success" | "budget" | "max_turns" | "api_error" | "error"),
    # budget_exhausted, transcript (the steps that arrived before a terminal error),
    # prompt_tokens, cache_hit_pct, cache_write_pct
```

**A terminal error is an outcome, not a crash.** The CLI ends a budget kill, a turn cap or
an API failure with an error `result` carrying the cost and turns accrued; the SDK raises
it as `ResultError` with that payload attached. `_run_sdk` returns the payload — and the
transcript it kept as the run streamed — so the dispatch is recorded with the spend that
caused it and the orchestrator sees what the worker did, not a traceback in place of it.
`dispatch.sh` exits **3** on a budget kill (`EXIT_BUDGET`), distinct from a worker that ran
and returned not-ok (1), so a pipe like `dispatch.sh … | tail` has something to notice.

## Tier resolution

First match wins:

| # | source | set by |
|---|---|---|
| 1 | explicit override | `--tier`, or `escalate.py` |
| 2 | policy | `--high-risk` forces up regardless of 3 |
| 3 | agent default | `model_tier:` in frontmatter |
| 4 | global default | `default_tier:` in `tiers.yaml` |

`Resolved.reason` records which applied. `check-model-config.sh` fails the build if an
agent names a tier that does not exist.

## `ok` is not "the model returned something"

```python
ok = (not payload["is_error"]
      and payload["subtype"] == "success"
      and not payload["permission_denials"])
```

Headless has no approver. A denied tool does **not** abort the run — the SDK denies the
call, the model continues, and the result returns `subtype: success`. A worker denied its
test command can and does report PASS. Any denial therefore marks the dispatch not-ok, and
the caller decides whether the task still completed.

## Injected prompt sections

`with_context()` prepends four blocks, in this order:

| section | contains | source |
|---|---|---|
| Technology context | the lane's stack/framework cards | `context.render_card(lane)` |
| Where you are | cwd, project root, harness root | `cwd` arg, `REPO`, `HARNESS` |
| Harness scripts | absolute paths, and why not `$VAR` | `HARNESS` |
| Permission requests already answered | operator rulings for this task | `broker.resolved_requests(task)` |

`cwd` is the **worktree** for an isolated agent, not the project root. Passing the project
root caused workers to go looking for their own location — 17 denials in one wave.

## Refusals

```python
require_sandbox()          # before anything else; raises SandboxUnavailable
if r.missing_env: raise DispatchError      # empty credential -> 401 that reads as an outage
if needs_worktree(agent) and cwd == REPO: raise DispatchError
```

## Telemetry

`record()` appends one event per dispatch through the Telemetry port:

```python
{"agent", "task", "attempt", "tier", "model", "effort", "max_budget_usd",
 "cost_usd", "input_tokens", "output_tokens", "cache_read_tokens",
 "cache_creation_tokens", "cache_hit_pct", "cache_write_pct", "models",
 "turns", "duration_ms", "ok", "terminal", "denied_tools",
 "env_names", "missing_env", "escalated_from"}
```

`cache_hit_pct` and `cache_write_pct` are shares of all prompt tokens the dispatch sent
(fresh + written + read). They are the numbers a cost analysis otherwise has to
reconstruct from transcripts by hand: workers in one wave that each start cold show as a
low hit rate; a resumed agent shows as ~0. `terminal` is why the run ended, so a tier
that keeps being killed by its ceiling is visible in `make models-cost` as `kills`.

`env_names` carries variable **names**, never values — `Resolved.redacted()` is the only
serialiser, because a provider token rendered into a record survives in the tracked export.

Recording failures are reported and swallowed: telemetry must never fail a dispatch that
already succeeded.

Read it back with `make models-cost`.

## CLI

```bash
harness/models/dispatch.sh <agent> --prompt-file F [options]

  --tier TIER        override (rank 1)
  --high-risk        force policy escalation
  --task ID          attaches telemetry + permission-request context
  --attempt N        re-dispatch counter
  --worker N         prepare and use an isolated worktree
  --lane LANE        selects the technology card
  --cwd PATH         explicit working directory
  --dry-run          resolve and print; spend nothing
  --no-record        skip telemetry
```

`--dry-run` prints the tier, model, budget, permission mode, grants, deny list and
readable directories. Use it before believing anything on this page.
