"""The guarded writer: only a person's session writes `harness.yaml`, one block's keys at a
time, the ledger on the answer, and a refusal rather than a guess."""

from __future__ import annotations

import json
import subprocess

import pytest
import yaml

import models.dispatch as dispatch
import models.project as project
from models import setup_write as sw
from models.setup_derive import Draft
from models.yaml_edit import Unanchorable

CONFIG = """\
# Owner's notes.
name: Fx
slug: fx

security:
  paths: [src/auth]     # the login boundary

areas:
  - path: src
    label: source
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "harness.yaml").write_text(CONFIG)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "i",
        ],
        check=True,
    )
    monkeypatch.setattr(project, "REPO", tmp_path)
    monkeypatch.setattr(project, "PROJECT_FILE", tmp_path / "harness.yaml")
    monkeypatch.setattr(dispatch, "WORKTREE_ROOT", tmp_path / ".claude" / "worktrees")
    for var in ("TRACKER_ACTOR", "SWARM_LANE", "HARNESS_ROOT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MAD_HARNESS_CALLER_PWD", str(tmp_path))
    return tmp_path


def _ledger(repo):
    p = repo / ".harness" / "setup.json"
    return json.loads(p.read_text()) if p.exists() else None


# --- the guard ----------------------------------------------------------------------------


@pytest.mark.parametrize("var", ["TRACKER_ACTOR", "SWARM_LANE"])
def test_a_worker_is_refused_and_nothing_is_written(repo, monkeypatch, var):
    monkeypatch.setenv(var, "swarm-w1")
    with pytest.raises(sw.Unattended, match=var):
        sw.write_block("areas", {"areas": []})
    assert (repo / "harness.yaml").read_text() == CONFIG


def test_a_linked_worktree_is_refused(repo, monkeypatch):
    wt = repo.parent / f"{repo.name}-wt"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", "--detach", str(wt)],
        check=True,
    )
    try:
        assert "linked worktree" in sw.refuse_unattended(
            env={"MAD_HARNESS_CALLER_PWD": str(wt)}
        )
        monkeypatch.setenv("MAD_HARNESS_CALLER_PWD", str(wt))
        with pytest.raises(sw.Unattended):
            sw.write_block("areas", {"areas": []})
    finally:
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)],
            check=True,
        )


def test_the_dispatchers_worktree_root_is_refused(repo):
    where = repo / ".claude" / "worktrees" / "w1"
    where.mkdir(parents=True)
    assert "worktree" in sw.refuse_unattended(
        env={"MAD_HARNESS_CALLER_PWD": str(where)}
    )


def test_the_primary_checkout_with_a_session_is_allowed_and_the_belt_only_reports(
    repo, monkeypatch
):
    """An operator's own interactive session sets CLAUDECODE too, so the belt alone must
    never refuse — it only reports."""
    env = {"MAD_HARNESS_CALLER_PWD": str(repo), "CLAUDECODE": "1"}
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert sw.refuse_unattended(env=env) is None
    assert "no terminal" in sw.unattended_hint(env=env)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert sw.unattended_hint(env=env) is None
    assert sw.unattended_hint(env={}) is None


# --- one block's keys ---------------------------------------------------------------------


def test_a_block_cannot_write_another_blocks_key(repo):
    with pytest.raises(PermissionError, match="may not write `security:`"):
        sw.write_block("areas", {"security": {"paths": []}})


@pytest.mark.parametrize("key", ["permissions", "dispatch"])
def test_setup_never_writes_an_unowned_key(repo, key):
    with pytest.raises(PermissionError, match="never written by setup"):
        sw.write_block("models", {key: {}})


# --- writing and the ledger ---------------------------------------------------------------


def test_a_write_with_no_answer_leaves_the_ledger_alone(repo):
    r = sw.write_block("areas", {"areas": [{"path": "src", "label": "application"}]})
    assert r.changed and r.ledger is None and _ledger(repo) is None
    assert "# the login boundary" in (repo / "harness.yaml").read_text()


def test_the_answer_records_the_block(repo):
    r = sw.write_block(
        "security",
        {
            "security": {
                "paths": ["src/auth"],
                "invariants": ["No tenant reads another's rows."],
            }
        },
        state="confirmed",
    )
    assert r.ledger["state"] == "confirmed"
    assert _ledger(repo)["blocks"]["security"]["hash"].startswith("sha256:")
    assert (
        "paths: [src/auth]     # the login boundary"
        in (repo / "harness.yaml").read_text()
    )


def test_the_same_write_twice_changes_nothing(repo):
    values = {"areas": [{"path": "src", "label": "application"}]}
    assert sw.write_block("areas", values).changed
    before = (repo / "harness.yaml").read_text()
    assert not sw.write_block("areas", values).changed
    assert (repo / "harness.yaml").read_text() == before


def test_a_decline_needs_a_reason_and_records_it(repo):
    with pytest.raises(ValueError):
        sw.write_block("frameworks", {"frameworks": []}, state="declined")
    r = sw.write_block(
        "frameworks",
        {"frameworks": []},
        state="declined",
        because="plain Python by design",
    )
    assert r.ledger["because"] == "plain Python by design"


def test_confirming_frameworks_snapshots_the_dependencies_itself(repo):
    (repo / "package.json").write_text('{"dependencies": {"next": "14"}}')
    r = sw.write_block("frameworks", {"frameworks": []}, state="confirmed")
    assert r.ledger["dependencies"] == {".": ["next"]}


def test_an_unanchorable_edit_writes_nothing(repo):
    text = CONFIG + "note: |\n  a block scalar\n"
    (repo / "harness.yaml").write_text(text)
    with pytest.raises(Unanchorable):
        sw._apply(text, {"note": "changed"}, [])
    assert (repo / "harness.yaml").read_text() == text


def test_the_cli_refuses_with_a_fragment_and_exit_3(repo, monkeypatch, capsys):
    (repo / "harness.yaml").write_text(CONFIG + "testing:\n  layout: |\n    prose\n")
    monkeypatch.setattr(
        "sys.stdin",
        __import__("io").StringIO(json.dumps({"testing": {"layout": "new"}})),
    )
    assert sw.main(["testing"]) == sw.EXIT_REFUSED
    out = json.loads(capsys.readouterr().out)
    assert yaml.safe_load(out["fragment"]) == {"testing": {"layout": "new"}}


# --- first run ----------------------------------------------------------------------------


def _drafts():
    return [
        Draft("identity", values={"name": "Tip Donkey", "slug": "tipdonkey"}),
        Draft("stacks", values={"stacks": ["python-uv"]}),
        Draft("areas", values={"areas": [{"path": "src", "label": "src"}]}),
        Draft(
            "tracker",
            values={
                "tracker": {"backend": "beads"},
                "beads": {"prefix": "TD"},
                "swarm": {"merge_slot": "TD-merge-slot"},
            },
        ),
    ]


def test_a_first_run_renders_a_loadable_config_with_no_template_value_left(repo):
    (repo / "harness.yaml").unlink()
    path = sw.render_first_run(_drafts())
    text = path.read_text()
    raw = yaml.safe_load(text)
    assert raw["name"] == "Tip Donkey" and raw["stacks"] == ["python-uv"]
    assert "acme" not in yaml.safe_dump(raw).lower(), (
        "a template value survived as a fact about this project"
    )
    assert text.startswith(
        "# harness.yaml — what makes this harness about THIS repository."
    )
    assert "security" not in raw and "ports" not in raw and "harness" not in raw, (
        "owed and stamp absent"
    )
    assert "# Toolchains: how to RUN things." in text, (
        "the template's explanations stay"
    )
    assert _ledger(repo) is None
    project.load(path)


def test_a_first_run_will_not_overwrite_an_existing_config(repo):
    with pytest.raises(FileExistsError):
        sw.render_first_run(_drafts())


# --- regressions from the pre-merge review -------------------------------------------------

SHARED = """\
name: Fx
slug: fx
stacks:
  - name: python-uv
    commands:
      test: uv run pytest -x   # the owner's choice
declined:
  stacks:
    rust@.: "not ours"
areas: []
"""


def test_declining_a_framework_keeps_the_declined_stacks(repo):
    (repo / "harness.yaml").write_text(SHARED)
    sw.write_block("frameworks", {"frameworks": [], "declined": {"frameworks": {"django@.": "no"}}})
    raw = yaml.safe_load((repo / "harness.yaml").read_text())
    assert raw["declined"] == {"stacks": {"rust@.": "not ours"}, "frameworks": {"django@.": "no"}}


def test_a_block_cannot_write_the_other_blocks_half_of_declined(repo):
    (repo / "harness.yaml").write_text(SHARED)
    with pytest.raises(PermissionError, match="only `declined.frameworks:`"):
        sw.write_block("frameworks", {"declined": {"stacks": {}}})


def test_the_stacks_block_keeps_command_overrides_it_does_not_mention(repo):
    (repo / "harness.yaml").write_text(SHARED)
    sw.write_block("stacks", {"stacks": ["python-uv", {"name": "node-npm", "root": "web"}]})
    stacks = yaml.safe_load((repo / "harness.yaml").read_text())["stacks"]
    assert stacks[0] == {"name": "python-uv", "commands": {"test": "uv run pytest -x"}}
    assert "# the owner's choice" in (repo / "harness.yaml").read_text()


def test_the_commands_block_cannot_change_which_toolchains_are_declared(repo):
    (repo / "harness.yaml").write_text(SHARED)
    with pytest.raises(PermissionError, match="only each stack's commands"):
        sw.write_block("commands", {"stacks": [{"name": "node-npm", "root": "web"}]})
    assert (repo / "harness.yaml").read_text() == SHARED


def test_an_unreadable_ledger_refuses_before_the_config_changes(repo, capsys, monkeypatch):
    (repo / ".harness").mkdir()
    (repo / ".harness" / "setup.json").write_text("{corrupt")
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps({"name": "Changed"})))
    assert sw.main(["identity", "--state", "confirmed"]) == 2
    assert "nothing was written" in json.loads(capsys.readouterr().out)["refused"]
    assert (repo / "harness.yaml").read_text() == CONFIG


def test_a_dispatched_agent_in_the_primary_checkout_is_refused(repo):
    """The headless campaign orchestrator runs in the primary checkout with no `.swarm-env`;
    the dispatcher's own HARNESS_ROOT is what marks it."""
    env = {"MAD_HARNESS_CALLER_PWD": str(repo), "HARNESS_ROOT": "/x/harness"}
    assert "dispatched agent" in sw.refuse_unattended(env=env)


def test_the_guard_fails_closed_outside_a_git_checkout(tmp_path):
    outside = tmp_path / "plain"
    outside.mkdir()
    assert "cannot tell" in sw.refuse_unattended(env={"MAD_HARNESS_CALLER_PWD": str(outside)})
