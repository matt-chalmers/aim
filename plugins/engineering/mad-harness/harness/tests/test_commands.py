"""Command resolution, the pre-flight probe, and the repair it writes.

Two properties carry most of the weight here, and both have a history:

  * **Every input produces an output.** `test_batching.py` pins this for `scan`
    and `peek`; the same rule applies to a batch of command runs, because an agent
    that silently receives fewer answers than it asked for reasons from a partial
    result believing it is whole.
  * **The repair stays inside the descriptive half of the config.** A mechanism
    that can edit `security.*` is a mechanism a worker could use to switch off the
    lens about to judge it. That boundary is asserted, not commented.
"""

from __future__ import annotations

import subprocess
import textwrap

import pytest
import yaml

from models.check_commands import (
    _NORMATIVE,
    _REPAIRABLE,
    Finding,
    probe_stack,
    write_repair,
)
from models.commands import (
    ABSENT,
    FAILED,
    NO_TOOL,
    OK,
    TIMEOUT,
    Outcome,
    resolve,
    run_key,
    tool_of,
)
from models.project import Stack


def mkstack(**kw) -> Stack:
    """A Stack with everything irrelevant to this file already filled in."""
    base = dict(
        name="demo",
        description="",
        detect_any=("marker",),
        dependency_dir=".venv",
        bootstrap={},
        env={},
        commands={},
    )
    base.update(kw)
    return Stack(**base)


@pytest.fixture(autouse=True)
def _logs_go_to_tmp(tmp_path, monkeypatch):
    """run.sh keeps every command's log under the CHECKOUT; in the suite that is the
    plugin's own tree. Ignored by git, but a test must not write there at all."""
    monkeypatch.setattr("models.commands.CHECKOUT", tmp_path)


def runner_returning(rc: int, err: str = "", out: str = ""):
    def _run(*a, **kw):
        return subprocess.CompletedProcess(a[0] if a else "", rc, out, err)

    return _run


def runner_that_hangs(*a, **kw):
    raise subprocess.TimeoutExpired(cmd="slow", timeout=1)


# --- resolution ---------------------------------------------------------------


def test_a_placeholder_is_substituted_and_a_bare_command_is_left_alone():
    s = mkstack(commands={"test_scoped": "pytest {path}", "test": "pytest"})
    assert resolve(s, "test_scoped", "tests/x.py") == "pytest tests/x.py"
    # No placeholder means the module is saying this command is not scopeable.
    # Appending the path anyway would invent a shape it never claimed to accept.
    assert resolve(s, "test", "tests/x.py") == "pytest"


def test_an_undeclared_key_resolves_to_nothing_rather_than_a_guess():
    assert resolve(mkstack(), "typecheck") is None


def test_tool_of_reads_the_executable_not_the_arguments():
    assert tool_of("uv run pytest") == "uv"
    assert tool_of("  npm test ") == "npm"
    assert tool_of("") == ""


# --- every input produces an output -------------------------------------------


def test_an_undeclared_key_is_reported_absent_never_dropped():
    """The batching invariant. A dropped input is worse than an extra call."""
    out = run_key(mkstack(commands={"test": "true"}), "lint")
    assert out.status == ABSENT
    assert "declares no 'lint'" in out.detail
    assert not out.ran


def test_a_missing_toolchain_is_its_own_diagnosis():
    """`no-tool` and `failed` send a reader to debug different things."""
    out = run_key(
        mkstack(commands={"test": "definitely-not-a-real-binary-xyz"}), "test"
    )
    assert out.status == NO_TOOL
    assert "not on PATH" in out.detail


def test_a_hang_is_never_reported_as_a_pass():
    """The recorded 8.5-hour stall was a command blocking on a prompt nobody saw."""
    out = run_key(mkstack(commands={"test": "true"}), "test", runner=runner_that_hangs)
    assert out.status == TIMEOUT
    assert not out.ok
    assert "hangs" in out.detail


def test_a_nonzero_exit_carries_the_runners_last_lines_and_where_the_rest_is(tmp_path, monkeypatch):
    monkeypatch.setattr("models.commands.CHECKOUT", tmp_path)
    out = run_key(
        mkstack(commands={"test": "true"}),
        "test",
        runner=runner_returning(1, "first line\nthe actual error"),
    )
    assert out.status == FAILED
    assert "the actual error" in out.detail and "full: .harness/run/out/demo-test.log" in out.detail
    assert "[stderr]\nfirst line\nthe actual error" in (tmp_path / ".harness/run/out/demo-test.log").read_text()


def test_a_failure_with_no_output_still_says_something(tmp_path, monkeypatch):
    monkeypatch.setattr("models.commands.CHECKOUT", tmp_path)
    out = run_key(
        mkstack(commands={"test": "true"}), "test", runner=runner_returning(3)
    )
    assert out.status == FAILED and out.detail.startswith("exit 3")


def test_a_long_failing_run_is_digested_to_the_failures_it_named_not_its_output(tmp_path, monkeypatch):
    """A tool result is paid on every later turn. Two field workers carried 28% of their
    prompt as their own results; before this, run.sh threw the output away and a worker
    that saw [FAIL] re-ran the suite raw to learn why."""
    monkeypatch.setattr("models.commands.CHECKOUT", tmp_path)
    noise = "\n".join(f"tests/test_x.py::test_{i} PASSED" for i in range(400))
    failing = "\n".join(
        [
            "tests/test_x.py::test_401 FAILED",
            "E   assert 1 == 2",
            "E    +  where 1 = f()",
            "FAILED tests/test_x.py::test_401 - AssertionError",
            "FAILED tests/test_x.py::test_402 - KeyError: 'k'",
            "==== 2 failed, 400 passed in 3.21s ====",
        ]
    )
    out = run_key(
        mkstack(commands={"test": "true"}), "test", runner=runner_returning(1, "", noise + "\n" + failing)
    )
    assert out.status == FAILED
    assert "FAILED tests/test_x.py::test_401" in out.detail and "test_402 - KeyError" in out.detail
    assert "E   assert 1 == 2" in out.detail and "2 failed, 400 passed" in out.detail
    assert "test_7 PASSED" not in out.detail and len(out.detail) < 1_200
    assert "full: .harness/run/out/demo-test.log (406 lines)" in out.detail
    assert "test_7 PASSED" in (tmp_path / ".harness/run/out/demo-test.log").read_text()


def test_a_passing_run_says_where_its_log_is_and_nothing_else(tmp_path, monkeypatch):
    monkeypatch.setattr("models.commands.CHECKOUT", tmp_path)
    out = run_key(mkstack(commands={"test": "true"}), "test", runner=runner_returning(0, "", "400 passed"))
    assert out.ok and out.detail == "log: .harness/run/out/demo-test.log"


def test_an_unwritable_log_dir_costs_the_path_not_the_run(tmp_path, monkeypatch):
    blocker = tmp_path / ".harness"
    blocker.write_text("a file where the directory should be")
    monkeypatch.setattr("models.commands.CHECKOUT", tmp_path)
    out = run_key(mkstack(commands={"test": "true"}), "test", runner=runner_returning(1, "boom"))
    assert out.status == FAILED and "boom" in out.detail and "full:" not in out.detail


# --- the ladder ---------------------------------------------------------------


def test_an_absent_verify_is_unproven_not_pass_and_not_fail():
    """Rung 1 passed, so the toolchain is here; nothing claims the command works.

    Reporting this as a pass would be the failure this whole check exists to
    prevent — a reassuring artefact standing in for a measurement.
    """
    findings = probe_stack(mkstack(commands={"test": "true"}))
    assert [f.status for f in findings] == ["unproven"]


def test_a_clean_probe_leaves_the_config_alone():
    s = mkstack(commands={"test": "true", "verify": "true"})
    findings = probe_stack(s, repair=True, runner=runner_returning(0))
    assert [f.status for f in findings] == ["ok"]


def test_a_missing_toolchain_is_reported_once_not_once_per_command():
    """One missing executable explains every command below it. Say it once.

    NOT "stops at rung one", which the old name claimed and the design contradicts:
    `_probe_reason` states that a missing executable "is NOT a stopping condition — a
    command naming a binary that does not exist is precisely the rotted case repair is
    for. Only rung 4 stops." So probing continues to rung 3 and derives candidates.

    Which is why the runner is INJECTED. With the real one, this test executed the
    candidates for real — `uv run pytest -q` and `make test`, this repository's own suite,
    nested inside its own run — for seven seconds, every time anyone ran the fast suite.
    The candidates it walks are what is under test here, not whether they happen to work
    on this machine.
    """
    s = mkstack(
        commands={"test": "nope-xyz t", "lint": "nope-xyz l", "verify": "nope-xyz v"}
    )
    findings = probe_stack(s, runner=runner_returning(1, "no such command"))
    assert len(findings) == 1
    assert findings[0].status == "broken"
    assert "not on PATH" in findings[0].detail


def test_no_working_candidate_is_the_only_thing_that_blocks(monkeypatch):
    import models.check_commands as mod

    monkeypatch.setattr(mod, "candidates", lambda s, k: [])
    s = mkstack(commands={"test": "true", "verify": "true"})
    findings = probe_stack(s, repair=True, runner=runner_returning(1, "boom"))
    assert [f.status for f in findings] == ["broken"]
    assert "no candidate found" in findings[0].detail


def test_the_candidate_itself_is_run_not_the_stacks_verify(monkeypatch):
    """A replacement is adopted on ITS OWN exit status.

    Re-running `verify` after swapping a candidate in would prove only that
    `verify` is still `verify` — it does not reference the command being replaced.
    """
    import models.check_commands as mod

    monkeypatch.setattr(
        mod, "candidates", lambda s, k: ["false still-broken", "true good-one"]
    )
    seen: list[str] = []

    def picky(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0 if "good-one" in cmd else 1, "", "")

    written: list[tuple] = []
    monkeypatch.setattr(mod, "write_repair", lambda *a, **k: written.append(a))
    s = mkstack(commands={"test": "false old", "verify": "false old"})
    findings = probe_stack(s, repair=True, runner=picky)
    assert [f.status for f in findings] == ["repaired"]
    assert findings[0].now == "true good-one"
    assert any("still-broken" in c for c in seen), "the loser must have been tried"
    assert any("good-one" in c for c in seen), "the winner must have been run itself"


def test_without_repair_a_fixable_command_is_reported_not_written(monkeypatch):
    import models.check_commands as mod

    monkeypatch.setattr(mod, "candidates", lambda s, k: ["true good-one"])
    boom = lambda *a, **k: pytest.fail("must not write without --repair")  # noqa: E731
    monkeypatch.setattr(mod, "write_repair", boom)

    def picky(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0 if "good-one" in cmd else 1, "", "")

    s = mkstack(commands={"test": "false old", "verify": "false old"})
    findings = probe_stack(s, repair=False, runner=picky)
    assert [f.status for f in findings] == ["broken"]
    assert "--repair" in findings[0].detail


# --- writing the repair -------------------------------------------------------


COMMENTED = textwrap.dedent("""\
    # A real harness.yaml is mostly comments explaining why each value is what it
    # is. Losing them would cost more than the repair is worth.
    name: Demo
    slug: demo

    stacks:
      - name: python-uv          # the toolchain, where this project keeps it
        root: services/api
        commands:
          # why this command and not another
          test_scoped: pytest {path}

    areas:
      - path: src
        label: source
    """)


def test_a_repair_keeps_the_comments_and_still_parses(tmp_path):
    """The regression the anchored-edit decision exists to prevent.

    `yaml.safe_dump` would round-trip this file into valid YAML with every comment
    gone, which is why the writer is a line-anchored replace instead.
    """
    p = tmp_path / "harness.yaml"
    p.write_text(COMMENTED)
    assert write_repair("python-uv", "test_scoped", "uv run pytest {path}", p)

    after = p.read_text()
    assert "uv run pytest {path}" in after
    assert "# why this command and not another" in after
    assert "# the toolchain, where this project keeps it" in after
    assert "mostly comments explaining" in after
    parsed = yaml.safe_load(after)
    assert parsed["stacks"][0]["commands"]["test_scoped"] == "uv run pytest {path}"
    assert parsed["stacks"][0]["root"] == "services/api"
    assert "auto-repaired" in after, "a config change must be traceable to this tool"


def test_a_repair_is_idempotent(tmp_path):
    p = tmp_path / "harness.yaml"
    p.write_text(COMMENTED)
    assert write_repair("python-uv", "test_scoped", "uv run pytest {path}", p)
    before = p.read_text()
    assert not write_repair("python-uv", "test_scoped", "uv run pytest {path}", p)
    assert p.read_text() == before, "a second run must not churn the file"


def test_a_bare_string_stack_is_promoted_to_carry_the_override(tmp_path):
    """`stacks: [python-uv]` is the common form; it must still be repairable."""
    p = tmp_path / "harness.yaml"
    p.write_text("name: D\nslug: d\nstacks:\n  - python-uv\nareas: []\n")
    assert write_repair("python-uv", "test", "uv run pytest", p)
    parsed = yaml.safe_load(p.read_text())
    assert parsed["stacks"][0] == {
        "name": "python-uv",
        "commands": {"test": "uv run pytest"},
    }


def test_a_stack_that_is_not_in_the_config_is_refused(tmp_path):
    p = tmp_path / "harness.yaml"
    p.write_text("name: D\nslug: d\nstacks:\n  - other\nareas: []\n")
    with pytest.raises(ValueError, match="not listed"):
        write_repair("python-uv", "test", "x", p)


def test_repair_refuses_a_normative_key(tmp_path):
    """THE BOUNDARY THAT MAKES AGENT-MAINTAINED CONFIG SAFE.

    `commands` is descriptive — a fact about the repo the agent just changed.
    `security`, `signals` and `testing.coverage` are the policy the agent is
    JUDGED BY. A mechanism able to edit those is one a worker could use to switch
    off the lens about to judge it, so the boundary is asserted here rather than
    left to whoever extends this next.
    """
    p = tmp_path / "harness.yaml"
    p.write_text(COMMENTED)
    for key in ("security", "security.invariants", "signals", "testing", "slug"):
        with pytest.raises(ValueError, match="agent-maintained"):
            write_repair("python-uv", key, "anything", p)
    assert "security" in _NORMATIVE and "signals" in _NORMATIVE
    assert _REPAIRABLE == ("commands",), (
        "widening this is a policy change, not a refactor — it decides what an "
        "agent may rewrite about the rules it is judged against"
    )


def test_the_guard_can_actually_fail(tmp_path):
    """A guard that cannot fail is worse than none.

    The refusals above would pass vacuously if `write_repair` refused everything,
    so prove a legitimate command key still writes.
    """
    p = tmp_path / "harness.yaml"
    p.write_text(COMMENTED)
    assert write_repair("python-uv", "test_scoped", "uv run pytest {path}", p)


# --- the finding's own rendering ----------------------------------------------


def test_a_repair_reports_before_and_after():
    """The owner has to be able to see what changed without reading the diff."""
    line = Finding("py", "repaired", key="test", was="old cmd", now="new cmd").line()
    assert "old cmd" in line and "new cmd" in line


def test_every_outcome_status_renders():
    for status in (OK, FAILED, ABSENT, NO_TOOL, TIMEOUT):
        assert Outcome(stack="s", key="k", status=status).line()


# --- the per-worker environment, which no grantable command could carry -------


def test_run_key_loads_the_workers_swarm_env(tmp_path, monkeypatch):
    """`worker-protocol` prescribed `source .swarm-env && <test command>`, and that form
    CANNOT BE PERMITTED: a compound command is denied even when every part of it is
    granted. Measured — `Bash(source:*)` plus `Bash(uv:*)` still refuses
    `source .swarm-env && uv run pytest`, while the plain command is allowed.

    So the env has to arrive some other way, or a worker's suite silently falls through to
    the shared default and parallel workers corrupt each other's fixtures while still
    going green.
    """
    import models.commands as mod

    (tmp_path / ".swarm-env").write_text(
        "# generated\nexport DB_NAME=proj_w3\nexport SWARM_LANE=backend\n"
    )
    monkeypatch.setattr(mod, "CHECKOUT", tmp_path)

    seen = {}

    def runner(cmd, **kw):
        seen["env"] = kw.get("env") or {}
        class R:
            returncode = 0
            stdout = stderr = ""
        return R()

    s = mkstack(commands={"test": "true"})
    mod.run_key(s, "test", runner=runner, check_tool=False)
    assert seen["env"].get("DB_NAME") == "proj_w3", "the worker's own database must survive"
    assert seen["env"].get("SWARM_LANE") == "backend"


def test_a_worktree_without_a_swarm_env_still_runs(tmp_path, monkeypatch):
    """The orchestrator's own checkout has no `.swarm-env`; absence is not an error."""
    import models.commands as mod

    monkeypatch.setattr(mod, "CHECKOUT", tmp_path)

    def runner(cmd, **kw):
        class R:
            returncode = 0
            stdout = stderr = ""
        return R()

    assert mod.run_key(mkstack(commands={"test": "true"}), "test",
                       runner=runner, check_tool=False).ok
