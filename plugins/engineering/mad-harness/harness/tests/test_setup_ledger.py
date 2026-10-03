"""The setup ledger: a review record that cannot disagree with the config about its values."""

from __future__ import annotations

import json

import pytest
import yaml

import models.project as project
from models.setup_blocks import by_id
from models.setup_ledger import (
    LedgerError,
    canonical_hash,
    dependency_drift,
    hash_stale,
    read,
    record,
    snapshot,
)

CONFIG = """\
name: Fixture   # the display name
slug: fixture
stacks:
  - python-uv
security:
  paths: [src/auth]      # where the boundary is
  tokens: [token]
areas:
  - path: src
    label: source
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(project, "REPO", tmp_path)
    return tmp_path


def _raw(text: str) -> dict:
    return yaml.safe_load(text)


def test_a_missing_ledger_reads_as_empty_not_an_error(repo):
    assert read() == {"version": 1, "blocks": {}}


@pytest.mark.parametrize(
    "content", ["{not json", json.dumps({"version": 99, "blocks": {}}), "[]"]
)
def test_a_corrupt_ledger_is_an_error_naming_the_file(repo, content):
    (repo / ".harness").mkdir()
    (repo / ".harness" / "setup.json").write_text(content)
    with pytest.raises(LedgerError, match="setup.json"):
        read()


def test_a_comment_or_reformat_does_not_change_the_hash():
    """The reason the hash is over the parsed value: an owner adding a comment must not see
    their confirmed block demoted."""
    reformatted = CONFIG.replace(
        "paths: [src/auth]      # where the boundary is",
        "# a new comment\n  paths:\n    - src/auth",
    )
    sec = by_id("security")
    assert canonical_hash(_raw(CONFIG), sec) == canonical_hash(_raw(reformatted), sec)
    assert canonical_hash(_raw(CONFIG), sec) != canonical_hash(
        _raw(CONFIG.replace("src/auth", "src/api")), sec
    )


def test_promoting_a_bare_stack_to_a_mapping_keeps_the_stacks_hash():
    promoted = CONFIG.replace(
        "  - python-uv", "  - name: python-uv\n    commands:\n      test: uv run pytest"
    )
    assert canonical_hash(_raw(CONFIG), by_id("stacks")) == canonical_hash(
        _raw(promoted), by_id("stacks")
    )


def test_record_writes_one_entry_with_the_installed_version(repo):
    entry = record("security", "confirmed", _raw(CONFIG), because="single-tenant today")
    on_disk = json.loads((repo / ".harness" / "setup.json").read_text())
    assert on_disk["blocks"]["security"] == entry
    assert (
        entry["at"] == project.plugin_version()
        and entry["because"] == "single-tenant today"
    )
    assert entry["hash"].startswith("sha256:")


def test_unhashed_blocks_record_no_hash_and_are_never_stale(repo):
    raw = _raw(CONFIG)
    assert record("models", "confirmed", raw)["hash"] is None
    raw["strengths"] = {"cheap": {"provider": "x"}}  # an owner's later model patch
    assert hash_stale(raw, read()) == []


def test_hash_stale_names_exactly_the_confirmed_blocks_edited_since(repo):
    raw = _raw(CONFIG)
    record("security", "confirmed", raw)
    record("areas", "confirmed", raw)
    record("identity", "defaulted", raw)
    edited = _raw(
        CONFIG.replace("src/auth", "src/api").replace("slug: fixture", "slug: other")
    )
    # identity changed too, but it was only defaulted — never a human's yes to that value.
    assert hash_stale(edited, read()) == ["security"]


def test_a_repaired_block_is_not_a_demotion(repo):
    raw = _raw(CONFIG)
    record("commands", "repaired", raw)
    raw["stacks"] = [{"name": "python-uv", "commands": {"test": "changed"}}]
    assert hash_stale(raw, read()) == []


@pytest.mark.parametrize("because", [None, "", "   "])
def test_a_decline_needs_a_reason(repo, because):
    with pytest.raises(ValueError, match="reason"):
        record("frameworks", "declined", _raw(CONFIG), because=because)


def test_an_unknown_state_or_block_is_refused(repo):
    with pytest.raises(ValueError):
        record("security", "owed", _raw(CONFIG))
    with pytest.raises(KeyError):
        record("orient", "confirmed", _raw(CONFIG))


def test_dependency_drift_reports_only_names_new_since_the_snapshot(repo):
    record(
        "frameworks",
        "confirmed",
        _raw(CONFIG),
        dependencies=snapshot({".": {"pyproject.toml": ["django", "requests"]}}),
    )
    now = {
        ".": {"pyproject.toml": ["django", "requests", "celery"]},
        "web": {"package.json": ["next"]},
    }
    assert dependency_drift(read(), now) == {".": ["celery"], "web": ["next"]}


def test_no_snapshot_means_no_drift_rather_than_everything_new(repo):
    record("frameworks", "defaulted", _raw(CONFIG))
    assert dependency_drift(read(), {".": {"package.json": ["next"]}}) == {}
    assert (
        dependency_drift(
            {"version": 1, "blocks": {}}, {".": {"package.json": ["next"]}}
        )
        == {}
    )


def test_the_file_is_sorted_and_newline_terminated_for_a_readable_diff(repo):
    record("security", "confirmed", _raw(CONFIG))
    text = (repo / ".harness" / "setup.json").read_text()
    assert (
        text.endswith("}\n")
        and text == json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"
    )
    assert not list((repo / ".harness").glob(".setup.*")), "a temp file was left behind"


def test_a_mapping_with_mixed_key_types_hashes_and_keeps_types_distinct():
    """`ports: {3000: web, backend: 8000}` crashed json's sort_keys — after the config was
    already written, so the block could never be confirmed."""
    lanes = by_id("lanes")
    assert canonical_hash({"ports": {3000: "web", "backend": 8000}}, lanes).startswith("sha256:")
    assert canonical_hash({"ports": {1: "a"}}, lanes) != canonical_hash({"ports": {"1": "a"}}, lanes)
