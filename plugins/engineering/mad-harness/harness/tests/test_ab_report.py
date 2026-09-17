"""`ab_report` — the A/B series read back with its spread, so noise cannot pass as a finding."""

from __future__ import annotations

import json

from models import ab_report


def _events(root, lever, arm, run, rows):
    p = root / f"{lever}-{arm}-{run}" / "beads" / ".harness" / "run" / "events"
    p.mkdir(parents=True)
    with (p / "harness.dispatch.jsonl").open("w") as fh:
        for r in rows:
            fh.write(json.dumps({"payload": {**r, "experiment": f"{lever}:{arm}:{run}"}}) + "\n")


def test_rows_are_grouped_by_arm_and_other_levers_are_ignored(tmp_path):
    _events(tmp_path, "cache_ttl", "off", 1, [{"cost_usd": 1.0, "ok": True}])
    _events(tmp_path, "cache_ttl", "on", 1, [{"cost_usd": 0.8, "ok": True}])
    _events(tmp_path, "static_prefix", "on", 1, [{"cost_usd": 9.0, "ok": True}])
    by_arm = ab_report.load(tmp_path, "cache_ttl")
    assert sorted(by_arm) == ["off", "on"] and by_arm["off"][0]["cost_usd"] == 1.0


def test_one_wild_run_does_not_become_the_result():
    """Medians and IQRs, never means: the cost analysis measured 30x variance on
    identical work."""
    rows = [{"_run": str(i), "cost_usd": c, "turns": 30, "ok": True} for i, c in enumerate([1.0, 1.1, 0.9, 1.0, 30.0])]
    s = ab_report.summarise({"off": rows})["off"]
    assert s["cost_per_run"][1] == 1.0, "the median ignores the 30x run"
    assert s["cost_per_run"][2] < 30.0


def test_the_verdict_refuses_to_call_overlapping_spreads_a_finding():
    assert "OVERLAP" in ab_report.verdict((0.8, 1.0, 1.2), (0.7, 0.9, 1.1), lower_better=True)
    assert "spreads separate" in ab_report.verdict((0.9, 1.0, 1.1), (0.5, 0.6, 0.7), lower_better=True)
    assert "-40% median, better" in ab_report.verdict((0.9, 1.0, 1.1), (0.5, 0.6, 0.7), lower_better=True)
    assert "worse" in ab_report.verdict((0.9, 1.0, 1.1), (1.5, 1.6, 1.7), lower_better=True)
    assert "better" in ab_report.verdict((10, 20, 30), (60, 70, 80), lower_better=False), "higher cache hit is better"
    assert ab_report.verdict(None, (1, 1, 1), True) == "no data"


def test_kills_are_counted_per_arm(tmp_path, capsys):
    _events(tmp_path, "task_budget", "off", 1, [{"cost_usd": 1.5, "ok": False, "terminal": "budget", "turns": 40}])
    _events(tmp_path, "task_budget", "on", 1, [{"cost_usd": 1.2, "ok": True, "terminal": "success", "turns": 35}])
    assert ab_report.main(["task_budget", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "1 budget kill(s)" in out and "budget kills      1 → 0" in out


def test_no_series_is_said_plainly(tmp_path, capsys):
    assert ab_report.main(["preload", "--root", str(tmp_path)]) == 1
    assert "no events for lever 'preload'" in capsys.readouterr().out
