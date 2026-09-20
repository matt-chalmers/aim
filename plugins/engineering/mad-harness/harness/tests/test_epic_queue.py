"""§1 and §2 as one call: the queue ordered, every exclusion with its reason, triage as a
pure function of three counts — and the lease that nothing in code held before."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from models import campaign_signals as cs
from models import epic_queue as mod
from models import wave_manifest as wm
from tracker.port import Task


def T(id, type="task", status="open", **kw):
    base = dict(id=id, type=type, status=status, title=kw.pop("title", id))
    base.update(kw)
    return Task(**base)


class Store:
    def __init__(self, tasks, gates=()):
        self.tasks = tasks
        self.gates = list(gates)

    def gate_list(self):
        return self.gates

    def list(self, *, type=None, status=None, parent=None, limit=None):
        rows = [t for t in self.tasks if (type is None or t.type == type) and (status is None or t.status == status) and (parent is None or t.parent == parent)]
        return rows

    def ready(self, *, parent=None, limit=None):
        return [t for t in self.tasks if t.parent == parent and t.status == "open" and t.acceptance and not t.depends_on]

    def show(self, tid):
        return next((t for t in self.tasks if t.id == tid), None)


def test_the_queue_is_p0_to_p3_then_oldest_and_exclusions_say_why():
    tasks = [
        T("E-3", "epic", priority=3, created_at="2026-01-01"),
        T("E-1", "epic", priority=1, created_at="2026-02-01"),
        T("E-2", "epic", priority=1, created_at="2026-01-15"),
        T("E-9", "epic", priority=0, notes="PARKED G-9: owner decision owed"),
        T("E-5", "epic", priority=2),
        T("E-7", "epic", priority=2),
    ]
    gates = [T("G-5", "gate", title="Gate: waiting on billing", depends_on=("E-5",))]
    leases = {"E-7": {"holder": "someone", "host": "other-mac", "at": 0}, "E-1": {"holder": "me", "host": "this-mac", "at": 0}}
    entries = mod.build(Store(tasks, gates), leases=leases, host="this-mac")
    assert [e.id for e in entries] == ["E-9", "E-2", "E-1", "E-5", "E-7", "E-3"], "P0 first, then P1 oldest first"
    by = {e.id: e for e in entries}
    assert by["E-9"].excluded.startswith("parked")
    assert by["E-5"].excluded == "gated: Gate: waiting on billing"
    assert by["E-7"].excluded == "leased by someone on other-mac"
    assert by["E-1"].excluded is None, "a lease THIS machine holds is a resumed run's own epic, not an exclusion"
    assert [e.id for e in mod.runnable(entries)] == ["E-2", "E-1", "E-3"]


def test_triage_is_a_pure_function_of_three_counts():
    assert mod.triage([], []) == ("UNPLANNED", 0, 0, 0)
    kids = [T("T-1", parent="E", acceptance=""), T("T-2", parent="E", acceptance="- AC1")]
    assert mod.triage(kids, [])[0] == "PARTIAL", "children but none ready"
    assert mod.triage([T("T-1", parent="E", acceptance="")], [T("T-1")])[0] == "PARTIAL", "ready but none carrying criteria"
    assert mod.triage(kids, [kids[1]]) == ("READY", 2, 1, 1)
    assert mod.triage([T("T-1", parent="E", status="closed", acceptance="x")], []) == ("UNPLANNED", 0, 0, 0), "closed children do not count"


def test_build_triages_every_runnable_epic_with_its_dispatchable_count():
    tasks = [T("E-1", "epic"), T("T-1", parent="E-1", acceptance="- AC1"), T("T-2", parent="E-1", acceptance="- AC1", depends_on=("T-1",))]
    e = mod.build(Store(tasks), leases={}, host="h")[0]
    assert (e.triage, e.children_open, e.with_criteria, e.ready) == ("READY", 2, 2, 1)
    text = mod.render([e], ["leases NOT checked — no remote"])
    assert "READY     E-1" in text and "1 ready / 2 with criteria / 2 open" in text and "note: leases NOT checked" in text


def test_the_wrapper_is_executable_and_runs_the_module():
    sh = Path(mod.__file__).resolve().parent.parent / "swarm" / "epic-queue.sh"
    assert sh.exists() and os.access(sh, os.X_OK) and "python -m models.epic_queue" in sh.read_text()


# --- signals --------------------------------------------------------------------------------


def test_gate_rate_counts_the_two_verdict_notes_and_the_audit_fails():
    assert cs.gate_rate("") == (0, 2, 0)
    assert cs.gate_rate("ADEQUACY: INFERABLE — …\nAUDIT: FAIL 2026-09-01 — slicing\nAUDIT: PASS 2026-09-02") == (2, 2, 1)


def test_the_signals_aggregate_the_waves_and_the_payload_carries_the_outcome_keys(tmp_path, monkeypatch):
    from models.project import Project

    monkeypatch.setattr(wm, "waves_dir", lambda base=None: tmp_path / "waves")
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", "."], cwd=r, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "fix: x"], cwd=r, check=True)
    p1 = wm.open_wave("E-1", lane="backend", planned=["T-1", "T-2"], dropped=[], wave_base="HEAD", base=tmp_path)
    wm.append(p1, "dispatched", {"agent": "fse"}, task="T-1")
    wm.append(p1, "dispatched", {"agent": "fse"}, task="T-2")
    wm.append(p1, "lenses", {"round": 1, "L1": "PASS", "L2": "PASS", "L3": "PASS", "L4": None, "touched_security_path": True, "l4_fired": False, "verified": True}, task="T-1")
    wm.append(p1, "closed", "T-1")
    wm.set_key(p1, "conflicts", [{"task": "T-2"}])
    wm.close(p1, head="HEAD")
    proj = Project(name="T", slug="t", stacks=(), paths={}, areas=(), security={}, raw={"signals": {"megafile_lines": 1000}})
    events = [{"task": "E-1", "tier": "strong", "cost_usd": 1.5, "ok": True}, {"task": "E-1.3", "tier": "worker", "cost_usd": 0.5, "ok": False}, {"task": "E-2", "tier": "worker", "cost_usd": 9, "ok": True}]
    sig = cs.compute("E-1", cwd=r, project=proj, notes="ADEQUACY: ADEQUATE", events=events, acceptance_for=lambda t: "", mode="auto")
    assert sig["first_pass_rate"] == 100 and sig["l4_dispatch_rate"] == 0 and sig["analyst_gate_rate"] == 50
    assert sig["wave_yield"] == 50 and sig["merge_conflicts"] == 1 and sig["dispatchable_on_entry"] == 2 and sig["waves"] == 1
    assert sig["escape_rate"] == 100, "one commit, and it is a fix:"
    assert sig["cost_by_tier"] == {"strong": {"dispatches": 1, "cost_usd": 1.5, "not_ok": 0}, "worker": {"dispatches": 1, "cost_usd": 0.5, "not_ok": 1}}, "the epic's own dispatches, incl. its tasks; not E-2's"
    pay = cs.payload(sig)
    assert pay["mode"] == "auto" and "cost_by_tier" not in pay and pay["l4_dispatch_rate"] == 0
    assert "①d analyst gate rate        50%" in cs.render(sig)


def test_the_two_blind_signals_now_have_bands():
    from tracker.campaign import CHECKS

    keys = {c[0] for c in CHECKS}
    assert {"l4_dispatch_rate", "analyst_gate_rate"} <= keys


# --- heartbeats ----------------------------------------------------------------------------


def test_the_heartbeat_is_written_by_the_script_that_runs_the_phase(tmp_path):
    p = wm.open_wave("E-1", lane="backend", planned=["T-1"], dropped=[], wave_base="b", base=tmp_path)
    calls = []

    def runner(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert wm.heartbeat(p, "DISPATCH", "n=1 ids=T-1", runner=runner) is None
    assert calls[0][1:3] == ["note", "E-1"] and calls[0][3].startswith("wave 1: DISPATCH n=1 ids=T-1 @")

    def bad(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, "", "record past the ceiling")

    assert "record past the ceiling" in wm.heartbeat(p, "GATE", runner=bad), "a failed write is said, never silent"


# --- decisions, ranked -----------------------------------------------------------------------


def test_decisions_are_ranked_by_what_they_unblock():
    from tracker.graph import rank_decisions

    tasks = [
        T("D-1", "decision", priority=2, created_at="2026-01-02"),
        T("D-2", "decision", priority=1, created_at="2026-01-01"),
        T("D-3", "decision", status="closed"),
        T("T-1", depends_on=("D-1",)), T("T-2", depends_on=("T-1",)), T("T-3", depends_on=("T-2",)),
        T("T-4", depends_on=("D-2",)), T("T-5", depends_on=("D-2",), status="closed"),
        T("E-1", "epic", notes="PARKED G-1: waiting on D-2"),
    ]
    ranked = rank_decisions(Store(tasks))
    assert [r["id"] for r in ranked] == ["D-2", "D-1"], "parking an epic outweighs a longer chain; a closed decision is not listed"
    by = {r["id"]: r for r in ranked}
    assert by["D-1"] == {"id": "D-1", "title": "D-1", "priority": 2, "created_at": "2026-01-02", "direct": 1, "unblocks": 3, "parks": []}
    assert by["D-2"]["unblocks"] == 1 and by["D-2"]["parks"] == ["E-1"], "a closed dependent is not unblocked work"
