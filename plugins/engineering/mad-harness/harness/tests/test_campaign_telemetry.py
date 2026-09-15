"""The campaign cost series — the one instrument for noticing the pipeline degrade.

Every record was filed as `campaign.epic_closed`, so an epic parked before a single wave
ran sat in the table beside completed ones with `beads_closed: 0`, and the trend was
drawn straight through both. The reading §6 asks for — "this queue is decision-blocked,
not slow" — was exactly what the series could no longer support.
"""

from __future__ import annotations

import pytest

from tracker import campaign
from tracker.events import LocalTelemetry


@pytest.fixture
def series(tmp_path, monkeypatch):
    tel = LocalTelemetry(root=tmp_path / "events")
    monkeypatch.setattr("tracker.telemetry", lambda: tel)
    return tel


def test_the_default_outcome_is_closed_so_old_rows_keep_their_meaning(series, capsys):
    assert campaign.main(["record", "E-1", '{"waves": 3, "beads_closed": 11}']) == 0
    assert "recorded campaign.epic_closed for E-1" in capsys.readouterr().out
    assert [e.category for e in series.read()] == ["campaign.epic_closed"]


def test_a_parked_epic_is_filed_under_its_own_category(series, capsys):
    rc = campaign.main(["record", "E-2", '{"dispatchable_on_entry": 3, "waves": 0}', "--outcome", "parked"])
    assert rc == 0
    assert "recorded campaign.epic_parked for E-2" in capsys.readouterr().out
    assert [e.category for e in series.read()] == ["campaign.epic_parked"]


def test_an_unknown_outcome_is_refused_not_filed_as_closed(series, capsys):
    assert campaign.main(["record", "E-3", "{}", "--outcome", "abandoned"]) == 2
    assert series.read() == []


def test_the_table_names_each_outcome_and_the_trend_reads_closed_epics_only(series, capsys):
    """Three closed epics with a flat dispatchable count, then two parked ones with a
    high one. The old reader would have drawn 'rising' through all five."""
    for i in range(3):
        campaign.record(f"C-{i}", {"dispatchable_on_entry": 6, "wave_yield": 80})
    campaign.record("P-1", {"dispatchable_on_entry": 1, "waves": 0}, "parked")
    campaign.record("P-2", {"dispatchable_on_entry": 2, "waves": 0}, "stopped")
    assert campaign.main([]) == 0
    out = capsys.readouterr().out
    assert "outcome" in out.splitlines()[0]
    assert "P-1" in out and "parked" in out and "stopped" in out, out
    assert "2 epic(s) parked or stopped" in out
    assert "last 3 closed epics" in out
    assert "6 → 6  (flat)" in out, out
    assert "→ 2" not in out, "a parked epic leaked into the trend"


def test_two_closed_epics_are_not_a_trend_however_many_were_parked(series, capsys):
    campaign.record("C-1", {"wave_yield": 80})
    campaign.record("C-2", {"wave_yield": 70})
    for i in range(4):
        campaign.record(f"P-{i}", {"dispatchable_on_entry": 1}, "parked")
    campaign.main([])
    out = capsys.readouterr().out
    assert "2 closed epic(s) recorded — a trend needs at least 3" in out, out
