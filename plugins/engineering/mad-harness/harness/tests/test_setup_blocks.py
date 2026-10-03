"""The setup block registry: one ordered list, and every config key owned by someone."""

from __future__ import annotations

import re

import pytest
import yaml

from models.project import PROJECT_FILE
from models.resolve import PLUGIN_ROOT
from models.setup_blocks import BLOCKS, UNOWNED, by_id, extract, owners, uncovered

TEMPLATE = PLUGIN_ROOT / "templates" / "harness.yaml.example"


def _template_keys() -> set[str]:
    """Every top-level key the template declares — live, or commented out as an opt-in block
    (`# fidelity:`, `# permissions:`, the model-config example)."""
    text = TEMPLATE.read_text()
    live = set((yaml.safe_load(text) or {}).keys())
    commented = set(re.findall(r"^# ?([a-z_]+):\s*$", text, re.M))
    return live | commented


def test_numbers_are_contiguous_and_ids_unique():
    assert [b.number for b in BLOCKS] == list(range(1, len(BLOCKS) + 1))
    assert len({b.id for b in BLOCKS}) == len(BLOCKS)
    for b in BLOCKS:
        assert by_id(b.id) is b
    with pytest.raises(KeyError):
        by_id("orient")  # not a block: it asks nothing and has no ledger entry


def test_every_top_level_key_has_an_owner():
    """A new schema key must be given to a block, or listed as deliberately unowned —
    otherwise setup silently never asks about it."""
    keys = _template_keys() | set(
        (yaml.safe_load(PROJECT_FILE.read_text()) or {}).keys()
    )
    assert "fidelity" in keys and "permissions" in keys, (
        "the commented opt-in blocks were not read"
    )
    assert not uncovered(keys), f"no block owns: {sorted(uncovered(keys))}"


def test_the_ownership_guard_fails_on_a_key_nobody_owns():
    assert uncovered({"name", "a_brand_new_block"}) == {"a_brand_new_block"}
    assert uncovered(set(UNOWNED)) == set()


def test_only_the_documented_keys_are_shared():
    shared = {k for b in BLOCKS for k in b.keys if len(owners(k)) > 1}
    assert shared == {"stacks", "declined"}


def test_a_bare_stack_and_its_promoted_mapping_are_the_same_entry():
    bare = {"stacks": ["python-uv"]}
    promoted = {"stacks": [{"name": "python-uv"}]}
    for bid in ("stacks", "commands"):
        assert extract(bare, by_id(bid)) == extract(promoted, by_id(bid))


def test_commands_are_split_out_of_the_stacks_block():
    before = {
        "stacks": [
            {"name": "node-npm", "root": "web", "commands": {"test": "npm test"}}
        ]
    }
    after = {
        "stacks": [
            {"name": "node-npm", "root": "web", "commands": {"test": "npm run test"}}
        ]
    }
    assert extract(before, by_id("stacks")) == extract(after, by_id("stacks"))
    assert extract(before, by_id("commands")) != extract(after, by_id("commands"))
    assert extract(after, by_id("commands")) == {
        "node-npm@web": {"test": "npm run test"}
    }


def test_declined_is_split_between_stacks_and_frameworks():
    raw = {
        "declined": {
            "stacks": {"node-npm@tools": "vendored"},
            "frameworks": {"django@.": "no"},
        }
    }
    assert extract(raw, by_id("stacks"))["declined"] == {"node-npm@tools": "vendored"}
    assert extract(raw, by_id("frameworks"))["declined"] == {"django@.": "no"}


def test_an_absent_key_is_absent_not_none():
    assert extract({"name": "x"}, by_id("identity")) == {"name": "x"}
