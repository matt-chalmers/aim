"""The batch primitives: many questions, one tool call.

These exist because of a measurement, not a hunch. Across four real lens runs, 92
bash calls were recorded and clustered: 31 searches, 16 slice reads, 11 reads at a
revision. At swarm.md's ~2,600 tokens per call, that overhead dominates the gate.

Both primitives share one non-negotiable property, and it is what these tests are
mostly about: **every input produces an output**. A batch that silently drops a
pattern or a path is worse than the N calls it replaced, because the agent cannot
see what it did not get back — it reasons from a partial answer believing it is
whole.
"""

from __future__ import annotations

import uuid

from verify.peek import peek_one
from verify.peek import render as peek_render
from verify.scan import render as scan_render
from verify.scan import scan_one

# --- scan: every pattern is answered -----------------------------------------


def absent_pattern() -> str:
    """A pattern guaranteed not to be in the tree — generated, never literal.

    The first version of these tests hard-coded a magic "definitely absent"
    string, which stopped being absent the moment the test file containing it was
    committed and `git grep` started finding it. A literal cannot express "not in
    this repository" in a file that lives in the repository; a fresh uuid can.
    """
    return f"absent-{uuid.uuid4().hex}"


def test_a_pattern_with_no_matches_is_reported_as_searched():
    """The case a lens most often cares about.

    "No stale reference remains" is answered by hits=0. Blank output would be
    indistinguishable from a pattern that was never run, so zero hits is stated
    in words rather than implied by absence.
    """
    r = scan_one(absent_pattern(), None, [], 12)
    assert r.hits == 0 and not r.error
    text = scan_render([r], 12, None, [])
    assert "NO MATCHES" in text and "searched and found nothing" in text


def test_every_pattern_appears_in_the_output_even_when_some_match():
    """A mixed batch must not let the matching patterns crowd out the empty ones."""
    pats = [absent_pattern(), "def ", absent_pattern()]
    results = [scan_one(p, None, ["harness"], 5) for p in pats]
    text = scan_render(results, 5, None, ["harness"])
    for p in pats:
        assert p in text, f"pattern {p!r} was searched but is missing from the report"


def test_match_listing_is_capped_but_the_count_is_exact():
    """Capping the listing is what keeps a batch cheaper than N calls; capping the
    COUNT would make the result a lie."""
    r = scan_one("e", None, ["harness/models"], 3)
    assert len(r.shown) <= 3
    assert r.hits > 3, "expected a common pattern to exceed the cap"
    assert f"{r.hits - 3} more not shown" in scan_render([r], 3, None, [])


def test_no_matches_is_not_treated_as_a_command_failure():
    """`git grep` exits 1 when it finds nothing. Treating that as an error would
    turn the most useful answer a lens gets into a broken tool."""
    assert scan_one(absent_pattern(), None, [], 12).error == ""


def test_a_bad_pattern_is_reported_not_swallowed():
    r = scan_one("[unclosed", None, [], 12)
    assert r.error, "an invalid regex must surface, not read as zero hits"
    assert "ERROR" in scan_render([r], 12, None, [])


# --- peek: every spec is answered --------------------------------------------


def test_a_missing_path_produces_a_stanza():
    """Six specs in, five stanzas out is the failure mode: the agent reasons about
    the sixth file having never read it."""
    c = peek_one("harness/definitely-not-here.txt", None, 50)
    assert c.error == "not found"
    assert "!! not found" in peek_render([c], None, 50)


def test_a_slice_reports_its_range_and_the_file_total():
    c = peek_one("harness/stacks/python-uv.yaml:1-4", None, 120)
    assert (c.start, c.end) == (1, 4)
    assert c.total > 14
    assert "lines 1-4 of" in peek_render([c], None, 120)


def test_a_range_past_the_end_of_the_file_is_clamped_not_an_error():
    c = peek_one("harness/stacks/python-uv.yaml:1-99999", None, 500)
    assert c.end == c.total and not c.error


def test_a_start_beyond_the_file_is_an_explicit_error():
    c = peek_one("harness/stacks/python-uv.yaml:99999", None, 120)
    assert "only" in c.error and "lines" in c.error


def test_reading_at_a_revision_works():
    c = peek_one("harness/models/resolve.py:1-3", "HEAD", 120)
    assert not c.error and c.lines


def test_a_path_absent_at_that_revision_says_so_with_the_rev():
    c = peek_one("harness/verify/scan.py", "21add61", 20)
    assert "not found at 21add61" in c.error


def test_output_carries_line_numbers_so_a_lens_can_cite_precisely():
    text = peek_render(
        [peek_one("harness/models/resolve.py:1-2", None, 120)], None, 120
    )
    assert "     1  " in text


def test_the_batch_total_cap_bounds_a_greedy_request():
    """`peek *.py` must not be able to blow the context window it was meant to save."""
    from verify.peek import TOTAL_CAP

    specs = [
        "harness/models/resolve.py",
        "harness/models/dispatch.py",
        "harness/verify/brief.py",
    ]
    text = peek_render([peek_one(s, None, 10_000) for s in specs], None, 10_000)
    body = [ln for ln in text.splitlines() if ln[:6].strip().isdigit()]
    assert len(body) <= TOTAL_CAP


def test_the_summary_names_the_specs_that_failed():
    chunks = [
        peek_one("harness/models/resolve.py:1-2", None, 120),
        peek_one("nope.txt", None, 120),
    ]
    tail = peek_render(chunks, None, 120).splitlines()[-1]
    assert "1/2 specs read" in tail and "nope.txt" in tail
