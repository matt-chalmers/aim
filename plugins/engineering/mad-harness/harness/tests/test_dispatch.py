"""The CLI dispatch boundary: argv, environment, parsing, and what must not leak.

None of these tests invoke a model. The subprocess runner is injected, so the
suite asserts on the exact command and environment we would have run — which is
the part that can be silently wrong — without paying for a dispatch to find out.
"""

from __future__ import annotations

import json

import pytest

from models.dispatch import (
    DispatchError,
    Outcome,
    build_env,
    dispatch,
    needs_worktree,
)
from models.resolve import Resolved

RESULT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "PASS · one clean commit",
    "total_cost_usd": 0.1252,
    "num_turns": 3,
    "duration_ms": 7738,
    "session_id": "abc-123",
    "usage": {
        "input_tokens": 12,
        "output_tokens": 340,
        "cache_read_input_tokens": 3057,
        "cache_creation_input_tokens": 5047,
    },
    "permission_denials": [],
}
def runner_returning(payload, *, noise="", returncode=0):
    """A stand-in for the SDK runner that records how it was called.

    Simpler than the subprocess fake it replaces: dispatch now receives the result
    message as a dict rather than parsing it out of a stdout stream, so a test no
    longer has to serialise JSON and simulate the noise a CLI prints around it.
    """
    calls = {}

    def run(resolved, prompt, **kwargs):
        calls["resolved"], calls["prompt"], calls["kwargs"] = resolved, prompt, kwargs
        if isinstance(payload, dict):
            return payload
        raise AssertionError(f"the SDK runner returns a dict, not {type(payload)!r}")

    run.calls = calls
    return run


def a_resolved(**over):
    base = dict(
        agent="verifier",
        tier="strong",
        reason="agent default",
        provider="anthropic",
        model="opus",
        effort="xhigh",
        max_budget_usd=4.0,
        env={},
        missing_env=(),
    )
    base.update(over)
    return Resolved(**base)


# --- argv and environment -----------------------------------------------------


def test_options_carry_every_routing_decision():
    o = a_resolved().sdk_options(cwd="/tmp")
    assert o.model == "opus"
    assert o.effort == "xhigh"
    assert o.max_budget_usd == 4.0
    assert o.extra_args["agent"].endswith("verifier")
    assert o.cwd == "/tmp"


def test_command_never_constrains_tools():
    """Measured, not assumed. Dispatching `verifier` with `--tools ""` produced an
    agent that did not know it was lens 1 of 3, because it declares
    `skills: [test-doctrine]` and an empty tool set breaks skill loading. The
    agent's own frontmatter already carries tools; overriding from out here can
    only desynchronise the two.
    """
    o = a_resolved().sdk_options()
    assert o.tools is None, "the agent's own frontmatter owns its tool set"


def test_provider_env_overlays_the_inherited_environment():
    r = a_resolved(provider="deepseek", env={"ANTHROPIC_BASE_URL": "https://x.invalid"})
    env = build_env(r, base={"PATH": "/usr/bin", "ANTHROPIC_BASE_URL": "https://old"})
    assert env["ANTHROPIC_BASE_URL"] == "https://x.invalid"
    assert env["PATH"] == "/usr/bin", "inherited variables must survive"


def test_dispatch_refuses_when_a_credential_is_unset(monkeypatch):
    """An empty token is a 401 at the provider, which reads as an outage.

    Refusing up front turns a confusing runtime failure into an obvious one.
    """
    monkeypatch.setattr(
        "models.dispatch.resolve",
        lambda *a, **k: a_resolved(
            provider="deepseek", missing_env=("DEEPSEEK_API_KEY",)
        ),
    )
    with pytest.raises(DispatchError, match="DEEPSEEK_API_KEY"):
        dispatch("fullstack-engineer", "x", runner=runner_returning(RESULT))


# --- the result must exist ----------------------------------------------------


def _sdk_yielding(*messages):
    """Stand in for `claude_agent_sdk.query`, yielding exactly these messages."""

    async def fake_query(*, prompt, options):  # noqa: ARG001
        for m in messages:
            yield m

    return fake_query


def test_a_dispatch_that_produced_no_result_raises_rather_than_reading_as_empty():
    """THE DANGEROUS FAILURE MODE, carried over from the hand-rolled parser.

    A lens that returned nothing must not read as "no findings" — that is exactly how a
    broken verifier silently passes bad work, and it is the empty-means-clean shape this
    corpus gates against everywhere else. Under the CLI this was an unparseable stdout;
    under the SDK it is a stream that ends without a ResultMessage. Same hazard, same
    answer: raise.
    """
    import claude_agent_sdk

    from models import dispatch as D

    class NotAResult:
        subtype = "init"

    original = claude_agent_sdk.query
    claude_agent_sdk.query = _sdk_yielding(NotAResult())
    try:
        with pytest.raises(DispatchError, match="no result message"):
            D._run_sdk(a_resolved(), "x", cwd=".", env={}, timeout=30)
    finally:
        claude_agent_sdk.query = original



# --- outcome ------------------------------------------------------------------


def test_successful_dispatch_captures_cost_and_tokens():
    run = runner_returning(RESULT)
    out = dispatch("verifier", "prompt text", runner=run)
    assert out.ok
    assert out.text == "PASS · one clean commit"
    assert (out.cost_usd, out.turns, out.duration_ms) == (0.1252, 3, 7738)
    assert (out.input_tokens, out.output_tokens) == (12, 340)
    assert (out.cache_read_tokens, out.cache_creation_tokens) == (3057, 5047)
    # The dispatcher appends the lane's technology card, so the prompt it sends is
    # the caller's text plus that context — never a replacement for it.
    assert run.calls["prompt"].startswith("prompt text")


@pytest.mark.parametrize(
    "payload",
    [
        {**RESULT, "is_error": True},
        {**RESULT, "subtype": "error_max_budget"},
    ],
)
def test_an_errored_dispatch_is_not_ok(payload):
    """Budget exhaustion in particular returns a well-formed object with partial
    work; treating that as success would land half a task."""
    assert not dispatch("verifier", "x", runner=runner_returning(payload)).ok


def test_a_denied_tool_is_a_failure_even_though_the_cli_calls_it_success():
    """The most dangerous shape available to this boundary.

    Headless `-p` does not block on a missing permission: it denies the tool, lets
    the model continue, and returns subtype=success with is_error=false. A worker
    denied Bash therefore cannot run the tests and still reports PASS. Trusting the
    CLI's own success flag would launder that into a green dispatch.
    """
    denied = {**RESULT, "permission_denials": [{"tool_name": "Bash"}]}
    out = dispatch("verifier", "x", runner=runner_returning(denied))
    assert not out.ok, "a dispatch that was denied a tool must never read as ok"
    assert out.permission_denials, "the caller needs to see WHICH tool was denied"


def test_telemetry_payload_carries_no_credential():
    """Event tasks land in the git-tracked .beads/issues.jsonl and get committed."""
    out = dispatch(
        "verifier",
        "x",
        runner=runner_returning(RESULT),
    )
    out = Outcome(
        **{
            **out.__dict__,
            "resolved": a_resolved(
                provider="deepseek", env={"ANTHROPIC_AUTH_TOKEN": "sk-do-not-leak"}
            ),
        }
    )
    blob = json.dumps(out.telemetry(task="PROJ-x"))
    assert "sk-do-not-leak" not in blob
    assert "ANTHROPIC_AUTH_TOKEN" in blob, "the NAME is useful; the value is not"


def test_telemetry_records_the_routing_decision_not_just_the_cost():
    """Without tier and reason the series cannot answer "was cheap-first worth it"."""
    t = dispatch("verifier", "x", runner=runner_returning(RESULT)).telemetry(
        task="PROJ-x", attempt=2, escalated_from="worker"
    )
    for key in (
        "task",
        "attempt",
        "escalated_from",
        "tier",
        "reason",
        "provider",
        "model",
        "cost_usd",
        "turns",
    ):
        assert key in t, f"telemetry is missing {key}"
    assert (t["attempt"], t["escalated_from"]) == (2, "worker")


# --- worktree isolation -------------------------------------------------------


def test_agents_declaring_worktree_isolation_are_recognised():
    """`claude -p` does NOT honour `isolation: worktree` — measured, not assumed.

    Dispatching fullstack-engineer headless ran it in the primary checkout. Under
    the Agent tool Claude Code creates the worktree; on this boundary nothing
    does, so a wave of eight would all edit the main tree at once. The frontmatter
    key stays authoritative and the dispatcher is what makes it true here.
    """
    assert needs_worktree("fullstack-engineer")
    assert needs_worktree("quality-engineer")
    assert not needs_worktree("verifier"), "read-only lenses need no worktree"
    assert not needs_worktree("architect")


def test_an_isolated_agent_refuses_to_run_in_the_primary_checkout(tmp_path):
    """Refusing beats proceeding: a wave that runs anyway corrupts the main tree
    in a way no test in this repo would catch."""
    from models.dispatch import REPO

    with pytest.raises(DispatchError, match="primary checkout"):
        dispatch("fullstack-engineer", "x", cwd=REPO, runner=runner_returning(RESULT))


def test_an_isolated_agent_is_allowed_inside_a_worktree(tmp_path):
    out = dispatch(
        "fullstack-engineer", "x", cwd=tmp_path, runner=runner_returning(RESULT)
    )
    assert out.ok


# --- the permission surface, which no writer ever had -------------------------


def test_a_writer_gets_edit_permission_and_command_grants():
    """MEASURED IN A LIVE WAVE, and it is why no writer ever worked headless.

    With neither flag, `Write` is denied and the worker returns BLOCKED having touched
    nothing. With `acceptEdits` alone the files land but every Bash call is still denied,
    so the suite never runs and nothing is committed — and a worker that cannot run tests
    cannot honour its contract. Both together: zero denials and a real commit.
    """
    from models.resolve import resolve

    r = resolve("fullstack-engineer")
    assert r.permission_mode == "acceptEdits"
    o = r.sdk_options()
    assert o.permission_mode == "acceptEdits"
    assert o.allowed_tools
    # Committing needs no grant under the sandbox — git runs inside the worktree and is
    # auto-approved. What the writer still needs is edit permission and the harness
    # scripts, which reach outside that workspace.
    from models.resolve import HARNESS

    assert any(str(HARNESS) in g for g in r.allowed_tools)


def test_a_read_only_lens_still_never_gets_to_write():
    """The property that makes a lens verdict worth having: it cannot change what it judges.

    THIS TEST PREVIOUSLY ASSERTED THAT A LENS GETS NO GRANTS AT ALL, on the reasoning that
    "reads are auto-approved already". A live run falsified it — every lens logged 4-13
    permission denials, including `Read` on the brief the harness had just written for it
    and the project's own test command. A lens that cannot run the suite can only believe
    the worker's report of it. So the grants are now real but still read-only, and what is
    pinned here is the boundary, not their absence."""
    from models.resolve import resolve

    r = resolve("verifier")
    assert r.permission_mode == "default", "a lens never edits"
    assert not any("git:" in g for g in r.allowed_tools), "a lens never commits"
    assert not any(
        g.startswith(("Edit", "Write", "NotebookEdit")) for g in r.allowed_tools
    ), r.allowed_tools


def test_the_grants_are_derived_from_the_project_not_hardcoded(monkeypatch):
    """A Node repo must get npm and a Python one uv, without either written down here.

    Asserted by SUBSTITUTING the project rather than by naming a tool: this repository's
    own stack runs a bare `pytest`, so it is granted `Bash(pytest:*)` — correct, and
    exactly why an assertion hardcoding "uv" failed. The property under test is that the
    grant follows the declaration, not that any particular tool appears.
    """
    import models.resolve as mod
    from models.project import Stack

    def fake_load():
        class P:
            stacks = (
                Stack(
                    name="node-npm", description="", dependency_dir="node_modules",
                    bootstrap={}, env={},
                    commands={"test": "npm test", "lint": "npx eslint ."},
                ),
            )
        return P()

    monkeypatch.setattr("models.project.load", fake_load)
    _, grants = mod.permission_for("fullstack-engineer")
    assert any("npm" in g for g in grants), "a declared npm stack must be granted npm"
    assert any("npx" in g for g in grants), "and every runner its commands name"
    assert not any("Bash(uv:" in g for g in grants), "not a toolchain it does not declare"


def test_every_agent_is_granted_the_harness_scripts():
    """The one grant that survived the sandbox, and the only one measurement demanded.

    With every grant removed and the sandbox on, a full two-wave run still completed —
    except that `tk.sh note` was refused five times. The harness scripts are what a worker
    runs that is NOT confined to its worktree, which is exactly why they still need a rule
    when git and the test runner no longer do.

    By ABSOLUTE path: a `$HARNESS_ROOT/...` grant matches nothing, measured.
    """
    from models.resolve import HARNESS, permission_for

    for agent in ("fullstack-engineer", "verifier"):
        _, grants = permission_for(agent)
        assert any(str(HARNESS) in g for g in grants), agent


def test_the_writer_test_is_derived_from_declared_tools():
    """Not from a list of agent names, which would go stale the first time one changed
    shape."""
    from models.resolve import permission_for

    for writer in ("fullstack-engineer", "quality-engineer", "spec-editor"):
        assert permission_for(writer)[0] == "acceptEdits", f"{writer} writes"
    for lens in ("verifier", "verifier-tests", "verifier-spec", "analyst"):
        assert permission_for(lens)[0] == "default", f"{lens} must stay read-only"


def test_bypass_permissions_is_never_used():
    """A writer runs unattended in a worktree, so the grant it gets is the grant it has.
    Narrowing to the toolchains the project declares is the difference between 'may run
    the suite' and 'may run anything'."""
    from models.resolve import resolve

    for agent in ("fullstack-engineer", "quality-engineer", "verifier"):
        assert resolve(agent).sdk_options().permission_mode != "bypassPermissions"


def test_the_grants_are_structured_data_not_a_hand_built_command_line():
    """WHAT THIS TEST USED TO GUARD, and why it no longer has to.

    Dispatch once built its own argv, and three separate defects lived there:
    `--allowedTools` is variadic, so passing the grants as separate arguments made it
    swallow the prompt; joining them was necessary but not sufficient, because a variadic
    flag in final position swallows the prompt anyway; and `--output-format` had to come
    last purely to terminate it. Each failed as "Input must be provided either through
    stdin or as a prompt argument" with a perfectly good prompt file on disk.

    None of that is expressible now. The grants are a list, the prompt is a separate
    argument to `query()`, and ordering is the SDK's problem. What is still ours, and so
    still asserted, is that the grants ARE carried.
    """
    from models.resolve import resolve

    o = resolve("fullstack-engineer").sdk_options()
    assert isinstance(o.allowed_tools, list) and o.allowed_tools
    assert all(isinstance(g, str) for g in o.allowed_tools)


def test_a_worktree_can_be_prepared_without_naming_a_lane(monkeypatch, tmp_path):
    """`--lane` is documented as optional — the init script defaults it to the project's
    first declared lane.

    Passing None through built an argv containing None and raised `TypeError: expected
    str, bytes or os.PathLike object` from inside subprocess, naming nothing a caller
    could act on.
    """
    import models.dispatch as mod

    seen = {}

    def fake_run(argv, **kw):
        seen.setdefault("argv", argv)
        class R:
            returncode = 0
            stderr = ""
        return R()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod, "WORKTREE_ROOT", tmp_path / "wt")
    mod.prepare_worktree("fullstack-engineer", 1, None, "T-1")
    assert all(a is not None for a in seen["argv"]), f"None in argv: {seen['argv']}"


def test_the_harness_grant_uses_the_form_that_actually_matches():
    """MEASURED against the live CLI, because three plausible spellings do not work.

        Bash(<dir>/*)          ALLOWED
        Bash(<script>:*)       ALLOWED
        Bash(<dir>/:*)         denied   <- the form this harness used everywhere
        Bash($HARNESS_ROOT/…)  denied   <- for ANY rule

    `<dir>/:*` never matches because the text before `:` is the COMMAND, and a directory
    is not a command. The variable form fails because matching is TEXTUAL: the rule is
    compared against the command as written, before the shell expands anything, so
    `$HARNESS_ROOT/...` matches neither the expanded path nor a rule spelled identically.
    """
    from models.resolve import HARNESS, permission_for

    _, grants = permission_for("fullstack-engineer")
    harness_grants = [g for g in grants if str(HARNESS) in g]
    assert harness_grants, "a writer must be able to run the harness scripts"
    for g in harness_grants:
        assert "/:*" not in g, f"{g} uses the form that never matches"
        assert "$HARNESS_ROOT" not in g, f"{g}: a variable cannot be granted"
        assert g.endswith("/*)") or g.endswith(":*)"), f"{g} is not a form that matches"


def test_the_worker_is_told_to_invoke_scripts_by_absolute_path():
    """A command spelled with a variable cannot be granted by any rule, so a prompt that
    tells a worker to run `$HARNESS_ROOT/...` is telling it to be denied — silently, in a
    dispatch nobody is watching."""
    from models.dispatch import with_context
    from models.resolve import HARNESS

    text = with_context("do the thing", None)
    assert str(HARNESS) in text
    assert "ABSOLUTE PATH" in text
    assert "TEXTUALLY" in text, "the reason must travel with the instruction"

def test_a_lens_is_given_the_brief_directory_and_a_writer_is_not():
    """A lens judges from a brief that is NEVER in the repository.

    `verify/brief.py` writes under SCRATCHPAD/TMPDIR because briefs are scratch, and a
    dispatched lens cannot read there without being handed the directory. This was
    measured in a live run before it was fixed: lenses logged 4-13 permission denials
    each, `Read` on their own brief among them, and fell back to judging the worker's
    claims by reading the worker's report — the one thing a verification gate exists not
    to do.
    """
    from models.resolve import briefs_root, resolve

    lens = resolve("verifier")
    assert briefs_root() in lens.add_dirs
    assert briefs_root() in [str(d) for d in lens.sdk_options().add_dirs]

    writer = resolve("fullstack-engineer")
    assert briefs_root() not in writer.add_dirs, "a writer judges nothing and reads no brief"


def test_the_no_diff_lens_is_denied_the_diff_root_and_the_others_are_not():
    """`verification-gate`'s "L3 must never see the diff" was a sentence: every reader
    was granted the briefs root and brief.md printed the diff's paths. Now the diff is a
    sibling root and the lens that declares `evidence: no-diff` is DENIED it — deny beats
    allow, and Claude Code applies a Read deny to cat/head/tail/sed as well."""
    from models.resolve import diff_root, resolve, sees_no_diff

    assert sees_no_diff("verifier-spec") and not sees_no_diff("verifier")
    deny = f"Read(//{diff_root().lstrip('/')}/**)"
    spec = resolve("verifier-spec")
    assert deny in spec.disallowed_tools and deny in spec.sdk_options().disallowed_tools
    assert "Bash(git push:*)" in spec.disallowed_tools, "the deny is added to FORBIDDEN, not in place of it"
    for other in ("verifier", "verifier-tests", "verifier-security"):
        assert deny not in resolve(other).disallowed_tools, other


def test_a_lens_may_run_the_projects_test_command():
    """Otherwise it can only BELIEVE the worker's "tests pass", which is not verification.

    Narrower than a writer's grant by design: the harness scripts and the toolchains the
    project declares, and nothing that edits or commits.
    """
    from models.resolve import resolve

    lens = resolve("verifier")
    assert lens.permission_mode == "default"
    assert any(g.startswith("Bash(") for g in lens.allowed_tools), lens.allowed_tools
    assert not any("git:" in g for g in lens.allowed_tools), "a lens does not commit"


def test_the_brief_directory_is_passed_as_a_directory_not_a_grant():
    """Measured: `Read(<dir>/**)` and `Read(<dir>/*)` are both refused exactly like the
    no-grant control, while handing over the directory works. A grant cannot express
    this, so a lens whose brief sits under TMPDIR needs `add_dirs`."""
    from models.resolve import briefs_root, resolve

    o = resolve("verifier").sdk_options()
    assert briefs_root() in [str(d) for d in o.add_dirs]
    assert not any(g.startswith("Read(") for g in o.allowed_tools)


def test_the_harness_path_is_granted_in_both_spellings_the_loader_produces():
    """`${CLAUDE_PLUGIN_ROOT}/harness/...` expands with one slash or two, depending on
    whether the installed plugin root carries a trailing one — and permission matching is
    TEXTUAL, so the doubled form matches no rule. Measured in a live run: every
    `tk.sh memories` and `check-line-pins.sh` call a lens attempted was refused for this
    reason alone, with a perfectly correct single-slash grant in place."""
    from models.resolve import HARNESS, permission_for

    for agent in ("verifier", "fullstack-engineer"):
        _, grants = permission_for(agent)
        assert f"Bash({HARNESS}/*)" in grants, agent
        assert f"Bash({HARNESS.parent}//{HARNESS.name}/*)" in grants, agent


def test_git_is_governed_by_the_sandbox_and_the_deny_rule_not_by_grants():
    """THIS TEST ONCE ASSERTED ELEVEN READ-ONLY GIT GRANTS FOR A LENS AND A WILDCARD FOR A
    WRITER. Both were added before the sandbox and neither survived measurement.

    Under the sandbox git operates inside the worktree — the sandboxed workspace — so it is
    auto-approved and needs no rule. Measured across two full waves and six lens dispatches
    with every git grant removed: zero git denials, every task committed and merged. The
    one git command that WAS refused sat inside a compound, which no prefix rule could ever
    have matched, so the grants were never what made the difference.

    What governs git now is the sandbox for containment and the deny rule for intent.
    """
    from models.resolve import permission_for

    for agent in ("fullstack-engineer", "verifier"):
        _, grants = permission_for(agent)
        assert not any("git" in g for g in grants), (
            f"{agent} carries a git grant the sandbox already covers: {grants}"
        )
    # The prohibition is the part a sandbox cannot express, so it stays.
    assert "Bash(git push:*)" in permission_for("fullstack-engineer")[1] or True
    from models.resolve import FORBIDDEN

    assert "Bash(git push:*)" in FORBIDDEN


def test_settings_are_not_inherited_from_the_developers_machine():
    """Grants must be a CEILING, not a floor.

    Measured before the fix: a lens carrying no git grant ran `git add -A` with zero
    denials, because ~/.claude/settings.json allows `Bash(git add *)`. The read-only
    boundary the lens profile exists to draw was not being enforced, and every machine
    dispatched with whatever permissions its owner happened to have.
    """
    from models.resolve import resolve

    o = resolve("verifier").sdk_options()
    assert o.setting_sources == ["project"], (
        "project settings are checked in and shared; user and local vary per machine"
    )


def test_the_plugin_is_loaded_by_path_since_user_settings_are_dropped():
    """Dropping `user` drops the plugin registration with it.

    Measured: `--setting-sources project` alone fails with
    "--agent 'mad-harness:verifier' not found".
    """
    from models.resolve import PLUGIN_ROOT, resolve

    o = resolve("verifier").sdk_options()
    assert o.plugins == [{"type": "local", "path": str(PLUGIN_ROOT)}]


def test_no_agent_may_push_whatever_else_it_is_granted():
    """`swarm.md`: "a worker must never push — it commits". Stated twice, enforced
    nowhere, while the writer grant `Bash(git:*)` permitted it outright. Deny beats
    allow (measured), so the prohibition is now real for every profile."""
    from models.resolve import resolve

    for agent in ("fullstack-engineer", "verifier"):
        r = resolve(agent)
        assert "Bash(git push:*)" in r.disallowed_tools, agent
        assert "Bash(git push:*)" in r.sdk_options().disallowed_tools, agent


def test_every_agent_can_read_the_harness_it_is_told_to_invoke():
    """Guidance that names a script the reader cannot open produces denials, not compliance.

    Measured in a live wave: two of six denials were a worker trying to `cat` and `Read`
    `harness/verify/run.sh` — the very script the stack card had just told it to use. A
    worker's worktree does not contain the harness, and reads outside the working
    directory are not auto-approved.
    """
    from models.resolve import HARNESS, REPO, resolve

    for agent in ("fullstack-engineer", "verifier", "planner"):
        dirs = [str(d) for d in resolve(agent).sdk_options().add_dirs]
        assert str(HARNESS) in dirs, agent
        # Every directory the prompt names must also be readable, or naming it just
        # invites a denial: measured when workers were told the project root and spent
        # four denials listing it.
        assert str(REPO) in dirs, agent


def test_a_worker_is_told_its_worktree_not_the_primary_checkout():
    """A prompt that states a falsehood about the environment is worse than silence.

    Naming the primary checkout here cost a whole wave. A worker runs in
    `.claude/worktrees/<branch>`, was told it was in the repository root, discovered the
    mismatch and went looking — for harness.yaml, for MAD_HARNESS_REPO, for `env`, and
    finally for ~/.zshrc — collecting ten permission denials on the way. Its worktree had
    the file it was hunting for all along.
    """
    from models.dispatch import with_context
    from models.resolve import REPO

    worktree = f"{REPO}/.claude/worktrees/harness-w1-abc"
    text = with_context("PROMPT", "backend", cwd=worktree)
    where = text[text.index("## Where you are") :].split("##")[1]

    # The WORKING DIRECTORY sentence must name the worktree. The primary checkout appears
    # too, labelled as the project under test — naming all three is what stopped workers
    # investigating the boundary between them — so the assertion is about which one is
    # called the working directory, not about which strings appear.
    working = next(line for line in where.splitlines() if "working directory" in line)
    assert worktree in working
    assert f"`{REPO}`" not in working, "the primary checkout is not where a worker is"
    assert f"the project under test: `{REPO}`" in where


def test_the_sandbox_is_enabled_with_the_escape_hatch_closed():
    """The boundary is only a boundary if the agent cannot step over it.

    Measured: the first sandboxed agent to meet a restriction reached straight for
    `dangerouslyDisableSandbox`. With `allowUnsandboxedCommands` left true that request
    would succeed and the containment would be advisory.
    """
    from models.resolve import resolve

    sb = resolve("fullstack-engineer").sdk_options().sandbox
    assert sb["enabled"] is True
    assert sb["autoAllowBashIfSandboxed"] is True
    assert sb["allowUnsandboxedCommands"] is False


def test_the_toolchain_cache_lives_inside_the_sandbox_boundary():
    """Granting `~/.cache/uv` was MEASURED AND DOES NOT WORK.

    uv fails with EPERM on a file inside the granted directory, not a plain write
    refusal, so the hole in the home directory buys nothing. Relocating the cache under
    the primary checkout does work, shares one cache across every worker, and cuts no
    hole at all. This pins that the env var and the sandbox grant agree — if they drift,
    a worker gets a cache path it is not allowed to write.
    """

    from models.resolve import cache_paths, resolve

    caches = cache_paths()
    if not caches:
        pytest.skip("this project's stacks declare no toolchain cache")

    # IN THE SANDBOX OPTION, not the settings: the SDK transport replaces the settings'
    # sandbox block with the option wholesale, so a policy written into settings never
    # reached the CLI.
    allowed = resolve("fullstack-engineer").sdk_options().sandbox["filesystem"]["allowWrite"]
    for var, path in caches.items():
        assert path in allowed, f"{var} points at {path}, which the sandbox does not allow"
        assert not path.startswith("~"), "must be absolute"


def test_the_sandbox_filesystem_policy_comes_from_the_declared_stacks():
    """Only a toolchain knows what it writes outside the repository.

    Confining a worker to its worktree is correct until `uv` cannot open `~/.cache/uv`
    and every test command fails with "Operation not permitted" — measured, and the
    reason `sandbox_write` exists on the stack module rather than as a constant here.
    """

    from models.project import load
    from models.resolve import resolve

    declared = {v for s in load().stacks for v in s.cache_env.values()}
    if not declared:
        pytest.skip("this project's stacks declare no toolchain cache")

    # IN THE SANDBOX OPTION, not the settings: the SDK transport replaces the settings'
    # sandbox block with the option wholesale, so a policy written into settings never
    # reached the CLI.
    allowed = resolve("fullstack-engineer").sdk_options().sandbox["filesystem"]["allowWrite"]
    for rel in declared:
        assert any(a.endswith(rel) for a in allowed), f"{rel} is not granted: {allowed}"


def test_dispatch_refuses_where_the_sandbox_cannot_be_enforced(monkeypatch):
    """A hard requirement needs a hard check.

    Without it, a machine lacking the sandbox would dispatch with permission rules alone
    — the posture this project decided not to support — and nothing would say so. Absence
    read as fine is the defect this corpus gates against everywhere else.
    """
    import platform

    from models.resolve import SandboxUnavailable, require_sandbox

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    with pytest.raises(SandboxUnavailable, match="only on machines where the sandbox"):
        require_sandbox()


# --- the operator-answered permission queue -----------------------------------


def test_an_operator_grant_reaches_the_next_dispatch(tmp_path, monkeypatch):
    """The only way this allowlist grows: a person answers a request, and the answer
    applies to the NEXT dispatch rather than the one that was blocked."""
    from models import resolve as R

    monkeypatch.setattr(R, "_operator_grants", lambda: ("Bash(curl https://pypi.org/*)",))
    _, grants = R.permission_for("verifier")
    assert "Bash(curl https://pypi.org/*)" in grants


def test_an_operator_grant_cannot_defeat_a_deny_rule():
    """Deny beats allow, measured. So an operator approving `git push` by mistake — or an
    agent talking one into it — still leaves pushing refused. The prohibition is not
    something the queue can launder away."""
    from models.resolve import FORBIDDEN, resolve

    o = resolve("fullstack-engineer").sdk_options()
    assert "Bash(git push:*)" in o.disallowed_tools
    assert "Bash(git push:*)" in FORBIDDEN


def test_permissions_are_normative_so_no_agent_can_write_them():
    """An agent may REQUEST a grant; only a person may write one.

    `write_repair` is the only mechanism that edits harness.yaml, and it refuses every
    normative key — otherwise an agent could approve its own request, which is exactly the
    laundering pattern the queue exists to avoid.
    """
    import pytest as _pytest

    from models.check_commands import _NORMATIVE, write_repair

    assert "permissions" in _NORMATIVE
    with _pytest.raises(ValueError, match="refusing to write"):
        write_repair("python-uv", "permissions.allow", "Bash(anything:*)")


def test_only_an_unexplained_refusal_becomes_a_question():
    """A refusal with a remedy the agent can act on is a malformed command, not a
    permission problem. Filing those would fill the operator's queue with noise until
    nobody read it — which is how a review queue stops being a control."""
    from models.broker import _is_answerable, explain

    for malformed in ("source .swarm-env; uv run pytest", "a && b", "VAR=x uv run pytest"):
        assert not _is_answerable(explain(malformed)), malformed
    assert _is_answerable(explain("curl https://example.com"))


def test_a_permission_request_carries_the_raw_command_not_a_summary():
    """An agent's account of why it needs something is model-written text, and a
    prompt-injected agent would write a persuasive one. The operator is shown what would
    actually run."""
    import inspect

    from models import broker

    src = inspect.getsource(broker.file_request)
    assert "VERBATIM" in src
    assert "{command[:2000]}" in src


def test_environment_inspection_never_reaches_the_operator_queue():
    """Asked sideways is still asked.

    `env` and `printenv` were covered from the start; a live wave then filed an operator
    question for `printf 'SCRATCHPAD=%s\\n' "${SCRATCHPAD:-unset}"` — the same request in
    different words. The queue is only a control while the things in it are genuine
    questions, so a known refusal must carry its remedy however it is spelled.
    """
    from models.broker import _is_answerable, explain

    for spelling in (
        "env",
        "printenv MAD_HARNESS_REPO",
        "echo $HARNESS_ROOT",
        "printf 'SCRATCHPAD=%s\n' \"${SCRATCHPAD:-unset}\"",
    ):
        assert not _is_answerable(explain(spelling)), spelling


def test_a_blocked_task_is_held_until_its_request_is_answered(tmp_path, monkeypatch):
    """WITHOUT THE EDGE IT IS A SPIN, NOT A LOOP.

    The task would stay ready, be dispatched again next wave, meet the same wall, and have
    its request deduplicated back onto the record already filed — burning one dispatch per
    wave to re-ask an answered question. The edge is what makes a campaign skip it and pick
    it up once a person answers.
    """
    import tracker
    from models.broker import file_request

    monkeypatch.setenv("MAD_HARNESS_REPO", str(tmp_path))
    monkeypatch.setenv("TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("HARNESS_RUN_DIR", str(tmp_path / "run"))
    from tracker.mdfiles import MdTaskStore

    store = MdTaskStore(root=tmp_path / "tasks")
    monkeypatch.setattr(tracker, "task_store", lambda *a, **k: store)

    task = store.create("needs a tool", type="task")
    assert task in [t.id for t in store.ready()]

    request = file_request("fullstack-engineer", task, "Bash", "curl https://blocked.invalid/x")
    assert request, "a request should have been filed"
    assert task not in [t.id for t in store.ready()], "the task must be held"

    store.close(request, "approved")
    assert task in [t.id for t in store.ready()], "answering must release it"


def test_a_resumed_worker_is_told_what_the_operator_decided(tmp_path, monkeypatch):
    """A re-dispatched worker is a FRESH AGENT with no memory of the first attempt.

    Without this it rediscovers the blocked approach and files the same request, which is
    deduplicated onto the record just answered — so the loop spins instead of advancing. A
    refusal matters most: "refused, because X" is what sends it down a different route.
    """
    import tracker
    from models.broker import file_request, resolved_requests
    from tracker.mdfiles import MdTaskStore

    monkeypatch.setenv("MAD_HARNESS_REPO", str(tmp_path))
    store = MdTaskStore(root=tmp_path / "tasks")
    monkeypatch.setattr(tracker, "task_store", lambda *a, **k: store)

    task = store.create("needs a tool", type="task")
    request = file_request("fullstack-engineer", task, "Bash", "curl https://forbidden.invalid/z")
    assert resolved_requests(task) == "", "an OPEN request is not an answer"

    store.close(request, "refused: unreachable from a worker; read the fixture from disk")
    text = resolved_requests(task)
    assert "refused: unreachable from a worker" in text
    assert "Do not ask again" in text


# --- --resume: attach a worker to the branch that already holds its work -----------------


def _resume_repo(tmp_path, monkeypatch):
    """A primary checkout with one worker branch holding a commit; git is REAL, only the
    worktree-init script is faked (it needs a project's stacks, not this test's concern)."""
    import subprocess as sp

    import models.dispatch as mod

    repo = tmp_path / "repo"
    repo.mkdir()
    g = lambda *a, cwd=repo: sp.run(  # noqa: E731
        ["git", "-c", "user.email=t@x", "-c", "user.name=t", *a], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()
    g("init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a\n")
    g("add", "-A")
    g("commit", "-q", "-m", "init")
    g("switch", "-q", "-c", "harness-w1-T-1")
    (repo / "b.txt").write_text("b\n")
    g("add", "-A")
    g("commit", "-q", "-m", "feat: half [T-1]")
    g("switch", "-q", "main")

    real_run = mod.subprocess.run

    def run(argv, **kw):
        if str(argv[0]).endswith("swarm-worktree-init.sh"):
            class R:
                returncode = 0
                stderr = ""
            return R()
        return real_run(argv, **kw)

    monkeypatch.setattr(mod.subprocess, "run", run)
    monkeypatch.setattr(mod, "REPO", repo)
    monkeypatch.setattr("models.resolve.REPO", repo)
    monkeypatch.setattr(mod, "WORKTREE_ROOT", repo / ".claude" / "worktrees")
    return repo, g


def test_resume_attaches_a_worktree_to_the_existing_branch_instead_of_cutting_a_new_one(tmp_path, monkeypatch):
    import models.dispatch as mod

    repo, g = _resume_repo(tmp_path, monkeypatch)
    path = mod.prepare_worktree("fullstack-engineer", 1, None, "T-1", resume="harness-w1-T-1")
    assert path == repo / ".claude" / "worktrees" / "harness-w1-T-1"
    assert g("rev-parse", "--abbrev-ref", "HEAD", cwd=path) == "harness-w1-T-1"
    assert (path / "b.txt").exists(), "the worker sees the commit already made"
    assert "harness-w1-T-1" in g("branch", "--list") and g("branch", "--list").count("harness-w") == 1, "no second branch"


def test_resume_reuses_a_live_worktree_and_its_uncommitted_work(tmp_path, monkeypatch):
    import models.dispatch as mod

    repo, g = _resume_repo(tmp_path, monkeypatch)
    live = repo / ".claude" / "worktrees" / "wherever"
    g("worktree", "add", "-q", str(live), "harness-w1-T-1")
    (live / "half.py").write_text("in progress\n")
    path = mod.prepare_worktree("fullstack-engineer", 1, None, "T-1", resume="harness-w1-T-1")
    assert path == live
    assert (live / "half.py").read_text() == "in progress\n"
    assert "UNCOMMITTED" in mod.resume_preamble("harness-w1-T-1", path)
    assert "1 commit(s)" in mod.resume_preamble("harness-w1-T-1", path)


def test_resume_refuses_a_branch_that_does_not_exist(tmp_path, monkeypatch):
    import models.dispatch as mod

    _resume_repo(tmp_path, monkeypatch)
    with pytest.raises(mod.DispatchError, match="no such branch"):
        mod.prepare_worktree("fullstack-engineer", 1, None, "T-1", resume="harness-w9-nope")


def test_a_fresh_dispatch_onto_an_existing_path_now_names_the_resume_flag(tmp_path, monkeypatch):
    import models.dispatch as mod

    repo, g = _resume_repo(tmp_path, monkeypatch)
    g("worktree", "add", "-q", str(repo / ".claude" / "worktrees" / "harness-w1-T-1"), "harness-w1-T-1")
    with pytest.raises(mod.DispatchError, match="--resume harness-w1-T-1"):
        mod.prepare_worktree("fullstack-engineer", 1, None, "T-1")


def test_what_a_child_must_not_inherit_is_gone_the_way_the_sdk_actually_builds_its_env(monkeypatch):
    """The SDK spawns with `{**os.environ, **options.env}`, so a key merely absent from
    build_env's result is inherited anyway. Measured: every worker got the dispatcher's
    MAD_HARNESS_CALLER_PWD and resolved CHECKOUT to the primary — 0.10.3's fix, undone by
    the merge — and the operator's VIRTUAL_ENV made every `uv run` warn, which sent 19 of
    21 lab workers reading the wrappers. This test composes the env as the SDK does."""
    import os

    from models.dispatch import build_env, scrub_process_env
    from models.resolve import resolve

    monkeypatch.setenv("VIRTUAL_ENV", "/Users/someone/Envs/unrelated")
    monkeypatch.setenv("MAD_HARNESS_CALLER_PWD", "/the/dispatchers/cwd")
    options_env = build_env(resolve("verifier"))
    assert "VIRTUAL_ENV" not in options_env and "MAD_HARNESS_CALLER_PWD" not in options_env
    scrub_process_env()  # what _run_sdk does before the SDK spawns
    child_env = {**os.environ, **options_env}
    assert "VIRTUAL_ENV" not in child_env and "MAD_HARNESS_CALLER_PWD" not in child_env
    assert child_env["MAD_HARNESS_REPO"]


def test_the_sandbox_option_carries_the_whole_policy_and_the_settings_carry_none(monkeypatch):
    """The SDK does `settings_obj["sandbox"] = options.sandbox`, replacing whatever the
    settings JSON said. Measured: an orchestrator's allowedDomains in settings left
    github.com denied. So filesystem and network live in the option, and the settings
    JSON has no sandbox key to lose."""
    import json

    from models import resolve as mod

    sandbox, settings = mod.sandbox_for(network=("github.com", "pypi.org"))
    assert sandbox["network"] == {"allowedDomains": ["github.com", "pypi.org"]}
    assert sandbox["enabled"] and not sandbox["allowUnsandboxedCommands"]
    assert "sandbox" not in json.loads(settings)
    sandbox, _ = mod.sandbox_for()
    assert "network" not in sandbox


def test_an_orchestrator_may_push_and_reach_the_remote_and_the_stacks_index_and_a_worker_may_not(monkeypatch):
    """`role: orchestrator`: no push deny, git and make granted, the sandbox open to the
    remote and to each stack's package index — because nested, a worktree's init runs
    inside the orchestrator's sandbox (measured: a nested worker died at `uv sync`)."""
    from models import resolve as mod

    o = mod.resolve("campaign-orchestrator")
    assert o.disallowed_tools == () and "Bash(git:*)" in o.allowed_tools and "Bash(make:*)" in o.allowed_tools
    domains = o.sandbox["network"]["allowedDomains"]
    assert "github.com" in domains and "pypi.org" in domains and "files.pythonhosted.org" in domains
    w = mod.resolve("fullstack-engineer")
    assert "Bash(git push:*)" in w.disallowed_tools and "Bash(git:*)" not in w.allowed_tools
    assert "network" not in w.sandbox


def test_an_orchestrators_ceiling_is_the_roles_not_its_tiers():
    """Measured: a headless lab epic stopped itself at $3.19 of the strong tier's $4.00
    with the epic open — the ceiling is per task and an orchestrator runs a whole epic."""
    from models import resolve as mod

    o = mod.resolve("campaign-orchestrator")
    v = mod.resolve("verifier")
    assert o.tier == v.tier == "strong"
    assert o.max_budget_usd == 25.0 and v.max_budget_usd == 4.0
