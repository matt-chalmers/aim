"""`ab_report` — the A/B series read back with its spread, so noise cannot pass as a finding."""

from __future__ import annotations

import json

from models import ab_report


def _events(root, lever, arm, run, rows):
    p = root / f"{lever}-{arm}-{run}" / "beads" / ".harness" / "run" / "events"
    p.mkdir(parents=True, exist_ok=True)
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


def test_lens_verdicts_are_read_per_arm_and_reported_as_first_pass_rates(tmp_path, capsys):
    """A lever that makes workers cheaper by doing less of the doctrine reads as a win on
    cost alone. Measured: the arm carrying test-doctrine ran mutation testing 4x as often
    and cost 61% more; only the lenses can say which arm's tests were worth having."""
    from models import ab_report
    from models.ab_report import load_verdicts, pass_rates

    for arm, rows in (("off", [("T1", "verifier", "PASS"), ("T1", "verifier-tests", "FAIL"), ("T1", "verifier-spec", "PASS")]),
                      ("on", [("T1", "verifier", "PASS"), ("T1", "verifier-tests", "PASS"), ("T1", "verifier-spec", "NONE")])):
        d = tmp_path / f"lever-{arm}-1" / "beads" / ".harness" / "run"
        (d / "events").mkdir(parents=True)
        (d / "lens-verdicts.txt").write_text("".join(f"{t}\t{a}\t{v}\n" for t, a, v in rows))
        _events(tmp_path, "lever", arm, 1, [
            {"cost_usd": 1.0, "ok": True, "terminal": "success", "turns": 10, "agent": "fullstack-engineer"},
            {"cost_usd": 0.5, "ok": True, "terminal": "success", "turns": 5, "agent": "verifier-tests"},
        ])
    v = load_verdicts(tmp_path, "lever")
    assert pass_rates(v["off"])["L2"] == (0, 1, 0) and pass_rates(v["on"])["L3"] == (0, 0, 1)
    assert ab_report.main(["lever", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "writers / run     $1.00" in out and "writers AND lenses" in out
    assert "L2 first-pass     0% → 100%" in out


def test_two_pockets_are_never_one_cost_per_run_and_never_one_delta(tmp_path, capsys):
    """MEASURED (the worker_provider series, 2026-09-23): the off arm spent $6.59 a run,
    all of it subscription allowance; the on arm moved the workers to a metered provider
    and spent $0.15 metered plus $5.12 subscription. The headline summed those to $5.27
    and called the difference '-20%, spreads separate' — a percentage between $6.59 and a
    figure no pocket paid. The subscription pocket in fact fell 22% and $0.15 of invoiced
    spend appeared, which is a different sentence and the true one."""
    _events(tmp_path, "worker_provider", "off", 1, [
        {"_run": "1", "cost_usd": 1.5, "billing": "subscription", "ok": True, "agent": "fullstack-engineer"},
        {"_run": "1", "cost_usd": 5.0, "billing": "subscription", "ok": True, "agent": "verifier"},
    ])
    _events(tmp_path, "worker_provider", "on", 1, [
        {"_run": "1", "cost_usd": 0.15, "billing": "metered", "ok": True, "agent": "fullstack-engineer"},
        {"_run": "1", "cost_usd": 5.0, "billing": "subscription", "ok": True, "agent": "verifier"},
    ])
    assert ab_report.main(["worker_provider", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out

    # The arm's own line is split, and the sum ($5.15) is stated nowhere.
    assert "$0.15   (IQR $0.15–$0.15)   metered" in out
    assert "$5.00   (IQR $5.00–$5.00)   subscription" in out
    assert "$5.15" not in out, "the two pockets must never be added into one cost / run"

    # The delta is per pocket, and the pocket only one arm uses is new spend, not a percent.
    assert "cost / run [subs]" in out and "-23% median" in out
    assert "cost / run [mete] $0.15 on the ON arm only — new spend in this pocket, not a delta" in out
    assert "\n  cost / run        -" not in out, "no single cross-pocket percentage"


def test_one_pocket_still_reports_one_cost_per_run(tmp_path, capsys):
    """The split must not leak into the ordinary case: a series inside one pocket reads
    exactly as it did before."""
    _events(tmp_path, "cache_ttl", "off", 1, [{"_run": "1", "cost_usd": 2.0, "billing": "subscription", "ok": True}])
    _events(tmp_path, "cache_ttl", "on", 1, [{"_run": "1", "cost_usd": 1.0, "billing": "subscription", "ok": True}])
    assert ab_report.main(["cache_ttl", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "cost / run        $2.00" in out and "subscription   —" not in out
    assert "cost / run        -50% median" in out and "cost / run [" not in out
