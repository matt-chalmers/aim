"""`tracker.staging` — either id form finds the epic's staged folder and its record.

Two scripts in one directory required opposite id forms and each failed plausibly on the
other's: `spec-index-status.sh PROJ-m7j7` said REBUILD for an index on disk; `render-epic.sh
m7j7` rendered "not planned yet" for an epic with 25 children.
"""

from __future__ import annotations

from tracker.mdfiles import MdTaskStore
from tracker.staging import id_forms, resolve_epic, staged_folder


def test_with_a_known_prefix_both_spellings_are_offered_as_given_first():
    assert id_forms("PROJ-m7j7", "PROJ") == ("PROJ-m7j7", "m7j7")
    assert id_forms("m7j7", "PROJ") == ("m7j7", "PROJ-m7j7")


def test_without_a_prefix_the_text_after_the_first_dash_is_the_bare_form():
    assert id_forms("PROJ-m7j7") == ("PROJ-m7j7", "m7j7")
    assert id_forms("m7j7") == ("m7j7",)
    assert id_forms("t-3fa2b1") == ("t-3fa2b1", "3fa2b1"), "a markdown-backend id is not special"


def test_a_bare_named_folder_is_found_from_the_prefixed_id_and_the_patterns_are_reported(tmp_path):
    (tmp_path / "m7j7-ingestion-change-detection").mkdir()
    (tmp_path / "m7j7x-other").mkdir()  # a near miss must not match
    folder, tried = staged_folder("PROJ-m7j7", tmp_path, prefix="PROJ")
    assert folder == tmp_path / "m7j7-ingestion-change-detection"
    assert tried[0].endswith("PROJ-m7j7*"), "the given form is tried first"


def test_a_prefixed_folder_is_found_from_the_bare_id(tmp_path):
    (tmp_path / "PROJ-m7j7-thing").mkdir()
    folder, _ = staged_folder("m7j7", tmp_path, prefix="PROJ")
    assert folder == tmp_path / "PROJ-m7j7-thing"


def test_a_miss_returns_nothing_and_every_pattern_it_tried(tmp_path):
    folder, tried = staged_folder("PROJ-zzzz", tmp_path, prefix="PROJ")
    assert folder is None and len(tried) == 2 and all("zzzz" in t for t in tried)


def test_an_epic_resolves_by_exact_id_or_by_unique_suffix(tmp_path):
    s = MdTaskStore(root=tmp_path / "t")
    e = s.create("the epic", type="epic")
    bare = e.split("-", 1)[1]
    assert resolve_epic(s, e) == e
    assert resolve_epic(s, bare) == e
    assert resolve_epic(s, "nope") is None


def test_two_epics_sharing_a_suffix_is_ambiguity_not_a_match(tmp_path):
    """Only possible across prefixes; the markdown backend mints unique tails, so build the
    collision by hand through the store's own files."""
    s = MdTaskStore(root=tmp_path / "t")
    a = s.create("one", type="epic")
    tail = a.split("-", 1)[1]
    b_path = tmp_path / "t" / f"OTHER-{tail}.md"
    b_path.write_text((tmp_path / "t" / f"{a}.md").read_text().replace(f"id: {a}", f"id: OTHER-{tail}"))
    assert resolve_epic(s, tail) is None
