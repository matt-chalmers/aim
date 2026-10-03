"""The leaf-level YAML editor: only changed lines change, and anything it cannot anchor is a
refusal with a fragment — never a corrupted file."""

from __future__ import annotations

import pytest
import yaml

import models.yaml_edit as ye
from models.resolve import PLUGIN_ROOT
from models.setup_blocks import stack_key
from models.yaml_edit import Unanchorable, remove_block, set_block

DOC = """\
# The project's config. A header comment that must survive everything.
name: Demo            # the display name
slug: demo

# Toolchains, with a reason for each.
stacks:
  # the API, in its own directory
  - name: python-uv
    root: api          # moved here in 2025
    commands:
      test: uv run pytest   # the whole suite
  - node-npm           # the root UI

# ---------------------------------------------------------------------------
# The security surface.
# ---------------------------------------------------------------------------
security:
  paths:
    - src/auth         # login and sessions
    - src/billing
  tokens: [token, secret]
  invariants:
    # stated by the owner, 2026-09
    - "No tenant reads another tenant's rows."

areas:
  - path: src
    label: source
    triggers: [security]
  - path: docs
    label: docs
"""

STACKS = {
    "stacks": stack_key,
    "areas": lambda a: a.get("path") if isinstance(a, dict) else a,
}


def _unchanged_lines(before: str, after: str) -> set[str]:
    return set(before.splitlines()) & set(after.splitlines())


def _set(text, key, value):
    return set_block(text, key, value, identity=STACKS.get(key))


def _parsed(text):
    return yaml.safe_load(text)


def test_a_changed_scalar_keeps_its_inline_comment_and_every_other_line():
    after = _set(DOC, "name", "Demo Co")
    assert "name: Demo Co            # the display name" in after
    assert after.replace("name: Demo Co ", "name: Demo ") == DOC


def test_a_nested_scalar_keeps_the_comments_inside_its_block():
    raw = _parsed(DOC)
    raw["stacks"][0]["commands"]["test"] = "uv run pytest -x"
    after = _set(DOC, "stacks", raw["stacks"])
    assert "      test: uv run pytest -x   # the whole suite" in after
    assert "  # the API, in its own directory" in after
    assert "    root: api          # moved here in 2025" in after
    assert len(after.splitlines()) == len(DOC.splitlines())
    assert sum(a != b for a, b in zip(DOC.splitlines(), after.splitlines())) == 1


def test_an_added_key_lands_inside_its_block_and_nothing_else_moves():
    raw = _parsed(DOC)["security"]
    raw["exempt"] = ["src/public"]
    after = _set(DOC, "security", raw)
    assert _parsed(after)["security"]["exempt"] == ["src/public"]
    assert all(ln in after for ln in DOC.splitlines())


def test_a_removed_key_takes_its_own_lines_and_no_others():
    raw = _parsed(DOC)["security"]
    del raw["tokens"]
    after = _set(DOC, "security", raw)
    assert "tokens" not in after
    assert set(DOC.splitlines()) - set(after.splitlines()) == {
        "  tokens: [token, secret]"
    }


def test_a_scalar_list_keeps_surviving_items_with_their_comments():
    raw = _parsed(DOC)["security"]
    raw["paths"] = ["src/auth", "src/admin"]
    after = _set(DOC, "security", raw)
    assert "    - src/auth         # login and sessions" in after
    assert "src/billing" not in after
    assert _parsed(after)["security"]["paths"] == ["src/auth", "src/admin"]


def test_a_reorder_moves_items_verbatim():
    raw = _parsed(DOC)["security"]
    raw["paths"] = ["src/billing", "src/auth"]
    after = _set(DOC, "security", raw)
    assert after.index("- src/billing") < after.index(
        "- src/auth         # login and sessions"
    )


def test_a_mapping_item_matched_by_identity_is_edited_in_place():
    raw = _parsed(DOC)["areas"]
    raw[1]["label"] = "documentation"
    after = _set(DOC, "areas", raw)
    assert "    label: documentation" in after
    assert "  - path: docs" in after and "    triggers: [security]" in after


def test_a_bare_stack_promoted_to_a_mapping_keeps_its_comment():
    raw = _parsed(DOC)["stacks"]
    raw[1] = {"name": "node-npm", "commands": {"test": "npm test"}}
    after = _set(DOC, "stacks", raw)
    assert "# the root UI" in after
    assert _parsed(after)["stacks"][1] == {
        "name": "node-npm",
        "commands": {"test": "npm test"},
    }
    assert "      test: uv run pytest   # the whole suite" in after


def test_a_flow_style_item_is_rewritten_on_its_own_line():
    text = "stacks:\n  - python-uv\n  - {name: node-npm, root: web}   # the UI\n"
    after = set_block(
        text,
        "stacks",
        ["python-uv", {"name": "node-npm", "root": "web", "cwd": "app"}],
        identity=stack_key,
    )
    assert "  - {name: node-npm, root: web, cwd: app}   # the UI" in after
    assert after.startswith("stacks:\n  - python-uv\n")


def test_a_new_and_a_removed_list_item():
    raw = _parsed(DOC)["areas"]
    raw = [raw[0], {"path": "harness", "label": "machinery"}]
    after = _set(DOC, "areas", raw)
    assert "path: docs" not in after and "label: machinery" in after
    assert "    triggers: [security]" in after


def test_a_flow_list_is_rewritten_on_its_own_line():
    raw = _parsed(DOC)["security"]
    raw["tokens"] = ["token", "secret", "session"]
    after = _set(DOC, "security", raw)
    assert "  tokens: [token, secret, session]" in after


def test_a_missing_top_level_key_is_appended_not_refused():
    after = _set(DOC, "ports", {"web": 3000})
    assert after.startswith(DOC) and _parsed(after)["ports"] == {"web": 3000}


def test_a_scalar_becomes_a_block_collection():
    text = "name: D\nslug: d\nfrontend: none   # nothing yet\n"
    after = set_block(text, "frontend", {"dir": "web"})
    assert "frontend:   # nothing yet" in after
    assert _parsed(after)["frontend"] == {"dir": "web"}


def test_a_collection_emptied_becomes_an_inline_empty_and_keeps_its_notes():
    after = set_block(DOC, "security", {"paths": [], "tokens": [], "invariants": []})
    assert _parsed(after)["security"] == {"paths": [], "tokens": [], "invariants": []}
    assert "# stated by the owner, 2026-09" in after


def test_remove_block_keeps_the_comments_above_it():
    after = remove_block(DOC, "security")
    assert "security" not in _parsed(after)
    assert "# The security surface." in after
    assert "src/auth" not in after


def test_writing_the_same_value_twice_changes_nothing():
    raw = _parsed(DOC)["areas"]
    raw[0]["label"] = "src"
    once = _set(DOC, "areas", raw)
    assert _set(once, "areas", raw) == once
    assert _set(DOC, "name", "Demo") == DOC


@pytest.mark.parametrize(
    "text, key, value, why",
    [
        ("name: D\nbase: &b {a: 1}\nother: *b\n", "base", {"a": 2}, "anchor"),
        (
            "name: D\nnote: |\n  line one\n  line two\n",
            "note",
            "changed",
            "block scalar",
        ),
        ("name: D\nlist: [a,\n  b]\n", "list", ["c"], "flow collection"),
        (
            "name: D\nlong: a plain scalar\n  continued here\n",
            "long",
            "short",
            "several lines",
        ),
        ("name: D\nsec:\n  a: 1\n  a: 2\n", "sec", {"a": 3}, "twice"),
    ],
)
def test_what_cannot_be_anchored_is_refused_with_a_fragment(text, key, value, why):
    with pytest.raises(Unanchorable) as exc:
        set_block(text, key, value)
    assert why in exc.value.why
    assert yaml.safe_load(exc.value.fragment) == {key: value}


def test_a_file_that_does_not_parse_is_a_refusal_not_a_crash():
    """Tab indentation, among others, is not YAML at all — refuse with the fragment."""
    with pytest.raises(Unanchorable, match="not valid YAML") as exc:
        set_block("name: D\nsec:\n\ta: 1\n", "sec", {"a": 2})
    assert yaml.safe_load(exc.value.fragment) == {"sec": {"a": 2}}


def test_the_post_condition_catches_a_walker_that_is_wrong(monkeypatch):
    """The companion that proves the guard can fail: break the region finder by one line and
    the editor must refuse, not write a corrupted file."""
    real = ye._edit_value

    def drops_a_line(region, indent, old, new, identity):
        return real(region, indent, old, new, identity)[:-1]

    monkeypatch.setattr(ye, "_edit_value", drops_a_line)
    raw = _parsed(DOC)["areas"]
    raw[0]["label"] = "changed"
    with pytest.raises(
        Unanchorable, match="exactly the requested block|invalid YAML|could not"
    ):
        _set(DOC, "areas", raw)


def test_every_block_of_the_real_template_round_trips_to_zero_changed_bytes():
    text = (PLUGIN_ROOT / "templates" / "harness.yaml.example").read_text()
    for key, value in yaml.safe_load(text).items():
        assert set_block(text, key, value) == text, key


def test_the_real_template_takes_a_value_change_in_every_block():
    """The template is the shape every first run starts from: each block must be editable."""
    text = (PLUGIN_ROOT / "templates" / "harness.yaml.example").read_text()
    for key, value in yaml.safe_load(text).items():
        if isinstance(value, dict) and value:
            first = next(iter(value))
            new = {**value, first: "changed"}
        elif isinstance(value, list) and value:
            new = value[:-1] or ["changed"]
        else:
            new = "changed"
        after = set_block(text, key, new, identity=STACKS.get(key))
        assert yaml.safe_load(after)[key] == new, key
        before_comments = [
            ln for ln in text.splitlines() if ln.lstrip().startswith("#")
        ]
        after_comments = [
            ln for ln in after.splitlines() if ln.lstrip().startswith("#")
        ]
        lost = set(before_comments) - set(after_comments)
        # Only a removed list item's own comment may go with it.
        assert len(lost) <= 1, (key, lost)


# --- regressions from the pre-merge review -------------------------------------------------


@pytest.mark.parametrize("line", ["docs: it's docs  # keep me", 'docs: "a \\" b"  # keep me', "docs: 'it''s'  # keep me"])
def test_a_quote_inside_a_value_never_costs_the_comment(line):
    """An apostrophe in a plain scalar was read as an opening quote, and the comment went
    with the old value; the post-condition compares values, so it could not notice."""
    assert set_block(line + "\n", "docs", "other") == "docs: other  # keep me\n"


def test_a_type_change_is_written_even_when_python_calls_the_values_equal():
    """`1 == True` in Python: asking for `true` over `1` returned the file unchanged."""
    after = set_block("testing:\n  parallel: 1   # n\n", "testing", {"parallel": True})
    assert after == "testing:\n  parallel: true   # n\n"
    assert yaml.safe_load(after)["testing"]["parallel"] is True
