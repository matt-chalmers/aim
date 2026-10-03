"""Openings and the owed queue: computed from the config, the ledger, the stamp and
discovery — one case per opening, and resumption without a stored queue."""

from __future__ import annotations

import subprocess

import pytest
import yaml

import models.project as project
import models.setup_state as st
from models.setup_blocks import BLOCKS
from models.setup_ledger import record, snapshot

WALKED = [b.id for b in BLOCKS if b.id != "stamp"]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setattr(project, "REPO", tmp_path)
    monkeypatch.setattr(project, "PROJECT_FILE", tmp_path / "harness.yaml")
    monkeypatch.setattr(project, "PROJECT_STACKS_DIR", tmp_path / ".harness" / "stacks")
    monkeypatch.setattr(
        project, "PROJECT_FRAMEWORKS_DIR", tmp_path / ".harness" / "frameworks"
    )
    monkeypatch.setattr(st, "plugin_version", lambda: "0.13.0")
    monkeypatch.setattr(project, "plugin_version", lambda: "0.13.0")
    return tmp_path


def _config(repo, version="0.13.0", **extra):
    raw = {
        "name": "x",
        "slug": "x",
        "areas": [],
        **({"harness": {"version": version}} if version else {}),
        **extra,
    }
    (repo / "harness.yaml").write_text(yaml.safe_dump(raw))
    return raw


def _review_everything(raw):
    for b in WALKED:
        record(b, "confirmed", raw)


def _queued(s):
    return [q["block"] for q in s.queue]


def test_no_config_renders_and_owes_every_walked_block(repo):
    s = st.compute()
    assert s.render_first_run and s.openings == ["first-run"]
    assert _queued(s) == WALKED and s.stamp_due


def test_a_config_with_no_ledger_owes_every_block_once(repo):
    _config(repo)
    s = st.compute()
    assert "unreviewed" in s.openings and _queued(s) == WALKED


def test_ahead_refuses_and_computes_nothing_else(repo):
    _config(repo, version="0.14.0")
    s = st.compute()
    assert "plugin is behind" in s.refuse and not s.queue and not s.openings


def test_behind_returns_the_notes_and_owes_the_stamp(repo):
    raw = _config(repo, version="0.11.0")
    _review_everything(raw)
    s = st.compute()
    assert "upgrade" in s.openings and s.stamp_due
    assert [n["version"] for n in s.notes][:1] == ["0.12.0"]


def test_patch_behind_with_everything_reviewed_is_a_re_stamp_and_asks_nothing(
    repo, monkeypatch
):
    monkeypatch.setattr(st, "plugin_version", lambda: "0.13.2")
    raw = _config(repo, version="0.13.0")
    _review_everything(raw)
    s = st.compute()
    assert s.upgrade == "patch-behind" and s.openings == ["re-stamp"]
    assert not s.queue and s.stamp_due


def test_everything_reviewed_and_current_owes_nothing(repo):
    raw = _config(repo)
    _review_everything(raw)
    s = st.compute()
    assert s.nothing_owed and not s.queue and not s.stamp_due


def test_an_undeclared_present_toolchain_owes_only_the_stacks_block(repo):
    raw = _config(repo)
    _review_everything(raw)
    (repo / "web").mkdir()
    (repo / "web" / "package-lock.json").write_text("")
    s = st.compute()
    assert _queued(s) == ["stacks"] and "revisit" in s.openings
    assert "node-npm@web" in s.queue[0]["reasons"][0]


def test_a_new_dependency_owes_frameworks(repo):
    raw = _config(repo)
    _review_everything(raw)
    record(
        "frameworks",
        "confirmed",
        raw,
        dependencies=snapshot({".": {"package.json": ["express"]}}),
    )
    (repo / "package.json").write_text(
        '{"dependencies": {"express": "4", "next": "14"}}'
    )
    s = st.compute()
    assert _queued(s) == ["frameworks"]


def test_repair_is_a_queue_of_one(repo):
    raw = _config(repo)
    _review_everything(raw)
    s = st.compute(["repair", "commands"])
    assert _queued(s) == ["commands"] and s.openings == ["repair"]


def test_a_hand_edit_owes_its_block(repo):
    raw = _config(repo)
    _review_everything(raw)
    _config(repo, slug="changed")
    s = st.compute()
    assert _queued(s) == ["identity"] and s.queue[0]["reasons"] == [
        "your edit, unconfirmed"
    ]


def test_an_interrupted_pass_resumes_at_the_first_unanswered_block(repo):
    """No stored queue: answering a block is what removes it."""
    raw = _config(repo)
    for b in WALKED[:3]:
        record(b, "confirmed", raw)
    s = st.compute()
    assert _queued(s) == WALKED[3:]


def test_an_explicit_revisit_re_examines_modules_and_raises_defaulted_blocks(repo):
    raw = _config(repo)
    _review_everything(raw)
    record("testing", "defaulted", raw)
    s = st.compute(["revisit"])
    assert _queued(s) == ["stacks", "frameworks", "testing"]


def test_the_cli_rejects_a_repair_without_a_known_block(repo, capsys):
    assert st.main(["repair", "nope"]) == 2


# --- regressions from the pre-merge review -------------------------------------------------


def test_a_machine_repair_stays_owed_until_a_person_confirms_it(repo):
    """The pre-flight's repair records `repaired` so it never WARNS — but it is not a
    review, and it took `commands` off the queue."""
    raw = _config(repo)
    _review_everything(raw)
    record("commands", "repaired", raw)
    assert _queued(st.compute()) == ["commands"]


def test_a_revisit_that_found_something_does_not_also_raise_defaulted_blocks(repo):
    raw = _config(repo)
    _review_everything(raw)
    record("testing", "defaulted", raw)
    (repo / "web").mkdir()
    (repo / "web" / "package-lock.json").write_text("")
    assert "testing" not in _queued(st.compute(["revisit"]))


def test_repair_names_only_a_walked_block(repo, capsys):
    assert st.main(["repair", "stamp"]) == 2


def test_an_unreadable_ledger_is_a_refusal_not_a_traceback(repo, capsys):
    _config(repo)
    (repo / ".harness").mkdir()
    (repo / ".harness" / "setup.json").write_text("{corrupt")
    assert st.main([]) == 2
    import json as _json
    assert "fix or remove it" in _json.loads(capsys.readouterr().out)["refuse"]
