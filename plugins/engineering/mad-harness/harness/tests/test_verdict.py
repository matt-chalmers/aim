"""One parser for what an agent concluded. A missing verdict is NONE, never PASS."""

from __future__ import annotations

import pytest

from models import verdict as mod
from models.verdict import FAIL, NONE, PASS, parse, return_status


def test_the_first_verdict_line_wins_with_its_qualifier():
    v = parse("some preamble\nVERDICT: PASS (no security surface)\n…\nVERDICT: FAIL\n")
    assert v.status == PASS and v.qualifier == "(no security surface)" and v.judged


@pytest.mark.parametrize("line", ["VERDICT: FAIL", "**VERDICT:** FAIL", "verdict — fail", "VERDICT: **FAIL**", "  VERDICT FAIL"])
def test_the_spellings_a_model_actually_produces_all_parse(line):
    assert parse(line + "\nFINDINGS: one blocking thing\n").status == FAIL


def test_no_verdict_line_is_NONE_never_pass():
    """`broker.py` records a lens that could not read its brief and still said PASS. A
    result that says nothing labelled is a result that judged nothing."""
    assert parse("").status == NONE
    assert parse("all tests pass and the criteria are met — PASS").status == NONE, "PASS in prose is not a verdict"
    assert parse("the verdict is that this passes").status == NONE
    assert not parse("").judged


def test_blocking_and_filed_findings_are_counted_not_judged():
    v = parse("VERDICT: FAIL\nFINDINGS:\n- blocking: caller X broken\n- filed: pre-existing hex literal\n- blocking: stale ADR\n")
    assert (v.blocking, v.filed) == (2, 1)


def test_a_test_shaped_fail_is_flagged_for_routing():
    assert parse("VERDICT: FAIL\nclassification: test-shaped — decorative assertion in test_x\n").test_shaped
    assert not parse("VERDICT: FAIL\nAC2 NOT MET\n").test_shaped


@pytest.mark.parametrize("line,expected", [
    ("PROJ-1 · PASS · CORE-CHANGE none · 3 files · 12 tests green · abc1234", "PASS"),
    ("PASS · PROJ-1 · …", "PASS"),
    ("PROJ-1 · BLOCKED · needs a decision", "BLOCKED"),
    ("PROJ-1 · NEEDS-SERIAL-LANE · touches the megafile", "NEEDS-SERIAL-LANE"),
    ("**PROJ-1** · **SKIPPED** · already claimed", "SKIPPED"),
    ("I did some work and it went fine", "NONE"),
    ("", "NONE"),
])
def test_the_worker_return_status_is_found_whichever_token_order(line, expected):
    assert return_status(line) == expected


def test_escalate_reads_through_this_parser():
    """Import identity: a second parser is how the two drift apart again."""
    from models import escalate

    assert escalate.return_status is mod.return_status


def test_a_heading_or_a_blockquote_is_formatting_not_a_different_verdict():
    """Measured (a lab wave, 2026-09-23): three lenses in one run opened `## VERDICT: PASS`
    — a heading — and each was read as NONE, which the gate treats as never-a-pass: ~$0.60
    of judgement discarded apiece and the task blocked by lenses that had passed it."""
    for text in ("## VERDICT: PASS", "# VERDICT: PASS", "### **VERDICT:** PASS",
                 "> VERDICT: PASS", "> ## VERDICT — PASS", "**VERDICT:** PASS", "VERDICT: PASS",
                 "`VERDICT: PASS`", "`VERDICT:` PASS", "## `VERDICT: PASS`"):
        v = mod.parse(text)
        assert v.status == mod.PASS, text
    assert mod.parse("## VERDICT: FAIL — the tests are decorative").status == mod.FAIL
    assert mod.parse("## VERDICT: PASS").status == mod.PASS
    # Still not a verdict: the word inside prose, or a status the contract does not define.
    assert mod.parse("The ## VERDICT: PASS was mentioned mid-sentence").status == mod.NONE, \
        "prose that quotes the word is not a verdict — the line must begin with it"
    assert mod.parse("## VERDICT: MAYBE").status == mod.NONE
    assert mod.parse("`VERDICT: PASS` (no security surface)").qualifier == "(no security surface)"
    assert mod.parse("`VERDICT: PASS`").qualifier == "", "a closing backtick is formatting, not a qualifier"
    assert mod.parse("I would pass this").status == mod.NONE
