"""§3 as a sequencer: the routing per verdict, the two interactive stops, the second-FAIL
park, the ABSENT path, the fresh planner on a revision, and the staging writes. The five
dispatches are faked by result text; the tracker and the scripts by an injected runner."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from models import plan_epic as mod
from models.project import Project
from models.steps import Raw
from tracker import staging
from tracker.port import Task


def key(argv):
    name = Path(argv[0]).name
    return f"{name} {argv[1]}" if name in ("tk.sh", "git") else (f"{name} {argv[2]}" if name == "spec-index-status.sh" and len(argv) > 2 else name)


class Runner:
    def __init__(self, **answers):
        self.answers = {
            "spec-index-status.sh": (0, "REBUILD — no staging folder; tried docs/proposed/E-1*\n", ""),
            "spec-index-status.sh --stamp": (0, "stamped\n", ""),
            "check-decision-register.sh": (0, "OK — 0 open, 1 settled\n", ""),
            "tk.sh update": (0, "", ""),
            "tk.sh create": (0, "D-9\n", ""),
            "tk.sh park": (0, "parked E-1 — gate G-1, status blocked\n", ""),
            "apply-plan.sh": (0, "applied 4 commands: 3 created, 0 already applied, 1 deps, 0 other; validate E-1: 2 waves, max parallelism 2\n", ""),
            "git rev-parse": (0, "abc1234\n", ""),
            # What a park's sync runs (tracker_sync.sync): export → view → add → commit → pull → push → status.
            "tk.sh backend": (0, json.dumps({"name": "mdfiles", "tracked_export": True, "owned_paths": ["docs/tasks/"], "export_path": "docs/tasks/issues.jsonl"}) + "\n", ""),
            "tk.sh export": (0, "", ""),
            "render-epic.sh": (0, "", ""),
            "git add": (0, "", ""),
            "git diff": (1, "", ""),
            "git commit": (0, "[main 1a2b3c4] chore(tracker): park E-1\n", ""),
            "git pull": (0, "Already up to date.\n", ""),
            "git rev-list": (0, "0\n", ""),
            "git push": (0, "", ""),
            "git status": (0, "## main...origin/main\n", ""),
        }
        self.answers.update({k.replace("_", " "): v for k, v in answers.items()})
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        a = self.answers[key(argv)]
        rc, out, err = a
        return subprocess.CompletedProcess(argv, rc, out, err)

    def keys(self):
        return [key(c) for c in self.calls]


def results_for(**by_agent):
    """A dispatch faker: writes the agent's result text and answers ok. A value of None
    means the dispatch failed (a denial); a list is consumed in order across calls."""
    seen = []

    def go(agent, prompt_file, out):
        seen.append((agent, prompt_file.read_text()))
        v = by_agent.get(agent, "")
        if isinstance(v, list):
            v = v.pop(0) if v else ""
        if v is None:
            return Raw(1, "", "permission denial")
        Path(out).write_text(v)
        return Raw(0, f"full: {out}", "")

    go.seen = seen
    return go


class Store:
    def __init__(self, notes=""):
        self.notes = notes

    def show(self, tid):
        return Task(id=tid, type="epic", status="open", title="Widgets epic", notes=self.notes)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    (r / "docs" / "proposed").mkdir(parents=True)
    (r / "docs" / "decisions").mkdir(parents=True)
    (r / "docs" / "decisions" / "0007-old.md").write_text("# ADR-0007\n")
    (r / "docs" / "features" / "widgets").mkdir(parents=True)
    (r / "docs" / "features" / "widgets" / "README.md").write_text("# Widgets\n")
    subprocess.run(["git", "init", "-q", "."], cwd=r, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init"], cwd=r, check=True)
    monkeypatch.setattr(mod, "REPO", r)
    monkeypatch.setattr("tracker.staging.known_prefix", lambda: "E")
    return r


def project():
    from models.project import Area

    return Project(name="T", slug="t", stacks=(), paths={"proposed": "docs/proposed", "adrs": "docs/decisions"},
                   areas=(Area(path="harness/models/", label="routing and dispatch", triggers=("security",)),), security={}, raw={"lanes": {"backend": {"cap": 4}}})


SURVEY = "TASK / EPIC: E-1\nMODE: survey\nADEQUACY: ADEQUATE\nAUTHORITATIVE SPEC: docs/features/widgets/README.md §2\nSPEC INDEX: docs/features/widgets/README.md — §2 governs\n  DECISIONS: ADR-0007 (binds the key)\nSPEC vs EPIC: agree\n"
DESIGN = "ARCHITECTURE: widgets get a service layer\n\nRecommended approach: …\nRejected: …\n"
PLAN = "DAG:\n- T1 SURFACE: none\n\n```bash\nT1: tk.sh create \"Add the model\" --parent E-1\n```\n"
AUDIT_PASS = "SPEC: E-1\nMODE: audit\nVERDICT: PASS\nFINDINGS: none\n"
AUDIT_FAIL = "SPEC: E-1\nMODE: audit\nVERDICT: FAIL\nFINDINGS: 1. blocking — AC2 not locatable\n"


def seq(repo, r, dispatch, mode="auto", store=None):
    return mod.Sequencer("E-1", mode=mode, project=project(), runner=r, cwd=str(repo), dispatch_fn=dispatch, store=store or Store())


def test_the_happy_path_in_auto_runs_every_stage_and_applies(repo):
    r = Runner()
    d = results_for(**{"analyst-survey": SURVEY, "architect": DESIGN, "planner": PLAN, "analyst": AUDIT_PASS})
    text, code = seq(repo, r, d).run()
    assert code == 0, text
    assert [a for a, _ in d.seen] == ["analyst-survey", "architect", "planner", "analyst"]
    notes = [c[4] for c in r.calls if key(c) == "tk.sh update" and "--append-notes" in c and c[3] == "--append-notes"]
    assert any(n.startswith("ADEQUACY: ADEQUATE") for n in notes)
    assert any(n.startswith("AUTO-ACCEPTED") and "design" in n for n in notes)
    assert any(n.startswith("AUDIT: PASS") for n in notes)
    assert any(n.startswith("AUTO-ACCEPTED") and "plan" in n for n in notes)
    assert any(key(c) == "tk.sh update" and "--append-notes-file" in c for c in r.calls), "the architect's output is attached by file, never retyped"
    folder = repo / "docs" / "proposed" / "1-widgets-epic"
    assert (folder / "spec-index.md").exists() and (folder / "design.md").exists()
    fm = (folder / "spec-index.md").read_text()
    assert "generated_sha:" in fm and "verdict: ADEQUATE" in fm and "  - docs/features/widgets/README.md" in fm
    assert "next free decision-record number is 0008" in d.seen[2][1]
    applied = [c for c in r.calls if c[0].endswith("apply-plan.sh")]
    assert applied and "--render" in applied[-1]
    assert r.keys()[-1] == "git status", "the sync (commit and push of the staging folder) follows the apply"


def test_interactive_stops_at_the_design_and_at_the_dag_with_the_resume_command(repo):
    r = Runner()
    d = results_for(**{"analyst-survey": SURVEY, "architect": DESIGN + "DECISION: which key?\n", "planner": PLAN, "analyst": AUDIT_PASS})
    text, code = seq(repo, r, d, mode="interactive").run()
    assert code == 6 and "APPROVAL OWED — the design" in text and "--from planner" in text and "which key?" in text
    assert [a for a, _ in d.seen] == ["analyst-survey", "architect"], "nothing after the design ran"
    assert not any(c[4].startswith("AUTO-ACCEPTED") for c in r.calls if key(c) == "tk.sh update" and c[3] == "--append-notes")
    text, code = seq(repo, r, d, mode="interactive").run("planner")
    assert code == 6 and "APPROVAL OWED — the DAG" in text and "--from apply" in text
    text, code = seq(repo, r, d, mode="interactive").run("apply")
    assert code == 0 and "PLANNED" in text


def test_an_absent_survey_drafts_then_audits_then_parks_with_the_requirement(repo):
    r = Runner()
    d = results_for(**{"analyst-survey": SURVEY.replace("ADEQUATE", "ABSENT"), "spec-editor": "drafted proposal.md", "analyst": "VERDICT: FAIL\nREQUIREMENT: no acceptance criteria exist for delivery failure\n"})
    text, code = seq(repo, r, d).run()
    assert code == 4 and "PARKED at absent" in text
    assert [a for a, _ in d.seen] == ["analyst-survey", "spec-editor", "analyst"], "draft, audit the draft, park — the architect never ran"
    created = [c for c in r.calls if key(c) == "tk.sh create"]
    assert created and created[0][2].startswith("REQUIREMENT: no acceptance criteria exist")
    park = [c for c in r.calls if key(c) == "tk.sh park"][0]
    assert "REQUIREMENT owed" in park[4] and "/requirements" in park[4]


def test_an_absent_survey_whose_draft_audits_adequate_continues_as_corpus_derived(repo):
    r = Runner()
    d = results_for(**{"analyst-survey": SURVEY.replace("ADEQUATE", "ABSENT"), "spec-editor": ["drafted", "folded in"], "analyst": [AUDIT_PASS, AUDIT_PASS], "architect": DESIGN, "planner": PLAN})
    text, code = seq(repo, r, d).run()
    assert code == 0, text
    notes = [c[4] for c in r.calls if key(c) == "tk.sh update" and c[3] == "--append-notes"]
    assert any(n.startswith("CORPUS-DERIVED — no owner input") for n in notes)


def test_an_architect_that_disputes_adequacy_parks_and_never_designs_in_auto(repo):
    r = Runner()
    d = results_for(**{"analyst-survey": SURVEY, "architect": "ADEQUACY: ABSENT\nREQUIREMENT: what a notification contains is unspecified\n"})
    text, code = seq(repo, r, d).run()
    assert code == 4 and "PARKED at architect" in text
    assert "planner" not in [a for a, _ in d.seen]
    assert any("auto-accept covers design, never invented scope" in c[4] for c in r.calls if key(c) == "tk.sh park")


def test_an_open_decision_from_the_architect_parks_in_auto(repo):
    r = Runner()
    d = results_for(**{"analyst-survey": SURVEY, "architect": DESIGN + "DECISION: per-user or per-IP limits?\n"})
    text, code = seq(repo, r, d).run()
    assert code == 4 and "the hard line" in text
    created = [c for c in r.calls if key(c) == "tk.sh create"]
    assert created[0][2] == "per-user or per-IP limits?" and created[0][3:5] == ["-t", "decision"]


def test_a_failed_audit_redispatches_the_planner_fresh_with_the_findings_and_a_second_fail_parks(repo):
    r = Runner()
    d = results_for(**{"analyst-survey": SURVEY, "architect": DESIGN, "planner": [PLAN, PLAN.replace("T1", "T1 revised")], "analyst": [AUDIT_FAIL, AUDIT_FAIL]})
    text, code = seq(repo, r, d).run()
    assert code == 4 and "failed its audit twice" in text
    agents = [a for a, _ in d.seen]
    assert agents == ["analyst-survey", "architect", "planner", "analyst", "planner", "analyst"]
    revision = d.seen[4][1]
    assert "This is a REVISION" in revision and "audit-1.md" in revision and "Fresh dispatch" in revision
    notes = [c[4] for c in r.calls if key(c) == "tk.sh update" and c[3] == "--append-notes"]
    assert sum(n.startswith("AUDIT: FAIL") for n in notes) == 2
    assert any(c[2].startswith("REQUIREMENT: the plan for E-1 cannot be made dispatchable") for c in r.calls if key(c) == "tk.sh create")


def test_a_failed_audit_then_a_pass_continues(repo):
    r = Runner()
    d = results_for(**{"analyst-survey": SURVEY, "architect": DESIGN, "planner": [PLAN, PLAN], "analyst": [AUDIT_FAIL, AUDIT_PASS]})
    text, code = seq(repo, r, d).run()
    assert code == 0 and "apply-plan.sh" in r.keys()


def test_a_dispatch_with_no_verdict_or_a_denial_is_could_not_judge_and_nothing_is_approved(repo):
    r = Runner()
    d = results_for(**{"analyst-survey": "MODE: survey\nno adequacy line here\n"})
    text, code = seq(repo, r, d).run()
    assert code == 2 and "no `ADEQUACY:` line" in text and "architect" not in [a for a, _ in d.seen]
    r = Runner()
    d = results_for(**{"analyst-survey": SURVEY, "architect": None})
    text, code = seq(repo, r, d).run()
    assert code == 2 and "did not return ok" in text and "tk.sh park" not in r.keys()


def test_reuse_skips_the_survey_when_the_epic_carries_the_verdict(repo):
    r = Runner(**{"spec-index-status.sh": (0, "REUSE — nothing it cites has moved.\n", "")})
    d = results_for(**{"architect": DESIGN, "planner": PLAN, "analyst": AUDIT_PASS})
    text, code = seq(repo, r, d, store=Store(notes="ADEQUACY: INFERABLE 2026-09-01 — two inferences")).run()
    assert code == 0 and "analyst-survey" not in [a for a, _ in d.seen] and "reused" in text


def test_a_delta_survey_stamps_the_baseline(repo):
    r = Runner(**{"spec-index-status.sh": (0, "DELTA — 1 cited doc(s) moved:\n- docs/features/widgets/README.md\n", "")})
    d = results_for(**{"analyst-survey": SURVEY, "architect": DESIGN, "planner": PLAN, "analyst": AUDIT_PASS})
    text, code = seq(repo, r, d).run()
    assert code == 0 and "spec-index-status.sh --stamp" in r.keys()
    assert "DELTA survey" in d.seen[0][1] and "docs/features/widgets/README.md" in d.seen[0][1]


def test_an_open_register_row_parks_before_the_architect(repo):
    from tracker.check_register import OPEN_MARKER

    r = Runner(**{"check-decision-register.sh": (0, f"── decisions.md\n   OK — 1 open, 0 settled  {OPEN_MARKER}\n", "")})
    d = results_for(**{"analyst-survey": SURVEY})
    text, code = seq(repo, r, d).run()
    assert code == 4 and "PARKED at register" in text and "architect" not in [a for a, _ in d.seen]


def test_a_ready_epic_gets_the_sanity_check_form(repo):
    r = Runner()
    d = results_for(**{"analyst-survey": SURVEY, "architect": DESIGN, "planner": PLAN, "analyst": AUDIT_PASS})
    s = seq(repo, r, d)
    s.state.data["triage"] = "READY"
    s.run()
    assert "SANITY-CHECK the existing design" in d.seen[1][1]


# --- the staging writes ----------------------------------------------------------------


def test_adr_next_and_the_staging_writes(repo):
    assert staging.adr_next(repo / "docs" / "decisions") == 8
    assert staging.adr_next(repo / "nowhere") == 1
    folder = staging.ensure_folder("E-1", repo / "docs" / "proposed", "Widgets epic", "E")
    assert folder == repo / "docs" / "proposed" / "1-widgets-epic" and folder.is_dir()
    assert staging.ensure_folder("1", repo / "docs" / "proposed", "renamed", "E") == folder, "found, not re-created under a new slug"
    d = staging.stage_design(folder, "E-1", "Recommended approach: a service layer.\n", title="Widgets epic")
    body = d.read_text()
    assert body.startswith("# Design: Widgets epic") and "**Status**: draft" in body and "service layer" in body
    d2 = staging.stage_design(folder, "E-1", "# Design: mine\n\nverbatim\n")
    assert d2.read_text() == "# Design: mine\n\nverbatim\n", "an output that carries its own title is kept verbatim"
    a = staging.draft_adr(folder, "E-1", "Per user or per IP?", "D-9")
    assert a.name == "adr-draft-1-per-user-or-per-ip.md" and "nothing may cite it as settled" in a.read_text()
    assert staging.cites_in("see docs/features/widgets/README.md and docs/missing.md and src/x.py", repo) == ["docs/features/widgets/README.md"]


def test_the_wrapper_is_executable_and_runs_the_module():
    sh = Path(mod.__file__).resolve().parent.parent / "swarm" / "plan-epic.sh"
    assert sh.exists() and os.access(sh, os.X_OK) and "python -m models.plan_epic" in sh.read_text()


def test_a_park_commits_and_pushes_the_tracker_state_and_never_restores_autosync(repo):
    """Measured (the first orchestrated wavelab run of 0.10.28): told only "parked — move to
    the next epic", the orchestrator spent 16 of 26 turns reading harness source to decide
    what to commit. The sequencer knows it parked; it syncs — and leaves autosync to
    campaign.sh / §5, as every wave does."""
    r = Runner()
    d = results_for(**{"analyst-survey": SURVEY, "architect": DESIGN + "DECISION: per-user or per-IP limits?\n"})
    text, code = seq(repo, r, d).run()
    assert code == 4
    keys = r.keys()
    park = keys.index("tk.sh park")
    tail = keys[park:]
    for k in ("tk.sh export", "git add", "git commit", "git push"):
        assert k in tail, f"{k} after the park"
    assert "tk.sh autosync" not in keys
    commit = next(c for c in r.calls if key(c) == "git commit")
    assert "park E-1 at architect" in " ".join(commit)
    add = next(c for c in r.calls if key(c) == "git add")
    assert "docs/tasks/issues.jsonl" in add, "the export is what the next session reads"
    assert "committed and pushed" in text and "campaign-signals.sh E-1 --outcome parked" in text


def test_a_successful_plan_commits_the_staging_folder_too(repo):
    """Measured: merge-wave.sh refused the first wave's merge on a dirty tree — the staged
    design and spec index from plan-epic were never committed on the success path."""
    r = Runner()
    d = results_for(**{"analyst-survey": SURVEY, "architect": DESIGN, "planner": PLAN, "analyst": "VERDICT: PASS\n"})
    text, code = seq(repo, r, d).run()
    assert code == 0, text
    keys = r.keys()
    assert keys.index("apply-plan.sh") < keys.index("git commit") < keys.index("git push")
    commit = next(c for c in r.calls if key(c) == "git commit")
    assert "plan E-1" in " ".join(commit)
    assert "committed and pushed" in text


def test_detach_runs_the_same_argv_through_fanout_and_wait_exits_5_while_running(repo, monkeypatch):
    from models import fanout
    from models import plan_epic as mod

    seen = {}
    monkeypatch.setattr(fanout, "detach", lambda jobs, cap: seen.update(jobs=jobs, cap=cap) or "20260921-000000-1")
    rc = mod.main(["E-1", "--mode", "auto", "--triage", "READY", "--detach"])
    assert rc == 0 and seen["cap"] == 1
    (job,) = seen["jobs"]
    assert job.argv[0].endswith("swarm/plan-epic.sh") and list(job.argv[1:]) == ["E-1", "--mode", "auto", "--triage", "READY"]
    assert "--detach" not in job.argv and job.task == "E-1"
    monkeypatch.setattr(fanout, "wait", lambda run_id, timeout: (False, [], []))
    assert mod.main(["--wait", "20260921-000000-1", "--timeout", "1"]) == 5
    done = fanout.JobResult(name="plan-epic E-1", argv=("x",), rc=4, status="done", seconds=3, stdout="PARKED at architect", stderr="")
    monkeypatch.setattr(fanout, "wait", lambda run_id, timeout: (True, [done], []))
    assert mod.main(["--wait", "20260921-000000-1"]) == 4


def test_no_push_parks_and_commits_without_pushing(repo):
    r = Runner()
    d = results_for(**{"analyst-survey": SURVEY, "architect": DESIGN + "DECISION: which?\n"})
    s = seq(repo, r, d)
    s.push = False
    text, code = s.run()
    assert code == 4 and "git commit" in r.keys() and "git push" not in r.keys()


def test_the_adequacy_verdict_tolerates_markdown_emphasis_and_the_line_forms():
    """Measured: `ADEQUACY: **ADEQUATE**` cost a survey re-dispatch. Emphasis around the
    word, or the label, is the same verdict; a different word is still none."""
    from models.plan_epic import ADEQUACY, DECISION_LINE, REQUIREMENT_LINE

    for text in ("ADEQUACY: ADEQUATE", "ADEQUACY: **ADEQUATE**", "**ADEQUACY:** ADEQUATE", "**ADEQUACY**: `INFERABLE`", "_ADEQUACY_: _ABSENT_"):
        m = ADEQUACY.search(text)
        assert m, text
    assert ADEQUACY.search("ADEQUACY: **ADEQUATE**").group("v") == "ADEQUATE"
    assert ADEQUACY.search("ADEQUACY: PERFECT") is None
    assert DECISION_LINE.search("**DECISION:** per-user or per-IP?").group("q") == "per-user or per-IP?"
    assert REQUIREMENT_LINE.search("**REQUIREMENT**: what happens on retry").group("q") == "what happens on retry"


def test_a_planned_epic_hands_the_architect_the_rendered_view_and_says_not_to_loop(repo):
    """Measured: the architect looped `for id in …; do tk.sh show; done` — denied, both
    attempts — and the sequencer stopped with nothing designed. The view is rendered by
    the sequencer for PARTIAL and READY; an UNPLANNED epic has no tasks to render."""
    r = Runner()
    d = results_for(**{"analyst-survey": SURVEY, "architect": DESIGN})
    s = seq(repo, r, d)
    s.state.data["triage"] = "PARTIAL"
    s.run()
    rendered = [c for c in r.calls if key(c) == "render-epic.sh"]
    assert rendered and rendered[0][1] == "E-1" and rendered[0][2] == "--write" and rendered[0][3].endswith("/tasks.md")
    arch_prompt = next(t for a, t in d.seen if a == "architect")
    assert "tasks.md" in arch_prompt and "never in a shell loop" in arch_prompt
    plan_prompt = next(t for a, t in d.seen if a == "planner")
    assert "tasks.md" in plan_prompt and "never in a shell loop" in plan_prompt, "the planner looped the same way, three of three attempts"
    assert "list --parent" not in plan_prompt
    r2 = Runner()
    d2 = results_for(**{"analyst-survey": SURVEY, "architect": DESIGN})
    s2 = seq(repo, r2, d2)
    s2.state.data["triage"] = "UNPLANNED"  # the state file still says PARTIAL from the run above
    s2.run()
    arch_prompt2 = next(t for a, t in d2.seen if a == "architect")
    assert "never in a shell loop" not in arch_prompt2


def test_could_not_judge_names_the_one_call_that_parks_and_syncs(repo):
    """Measured: exit 2 said only "re-run once the cause is fixed"; the orchestrator re-ran
    the same stage three times unchanged, then parked and synced by hand in 13 turns."""
    r = Runner()
    d = results_for(**{"analyst-survey": SURVEY, "architect": None})
    text, code = seq(repo, r, d).run()
    assert code == 2
    assert "halt.sh pause E-1" in text and "--from architect" in text and "Do not re-run the stage unchanged" in text


# --- proportionality: planning must cost less than the work it plans -----------------------


class StoreWithChildren(Store):
    """An epic with open children; `ready` marks them READY by the planner's checks."""

    def __init__(self, n=3, ready=True, notes=""):
        super().__init__(notes)
        self.kids = [
            Task(id=f"E-1.{i}", type="task", status="open", title=f"Task {i}", parent="E-1",
                 description=f"Do thing {i}.\nSURFACE: none" if ready else f"Do thing {i}.",
                 acceptance="- it works" if ready else "")
            for i in range(1, n + 1)
        ]

    def list(self, parent=None, **kw):
        return list(self.kids) if parent == "E-1" else []


VALIDATE_CLEAN = (0, json.dumps({"ok": True, "waves": [{"index": 1, "task_ids": ["E-1.1", "E-1.2", "E-1.3"]}], "contention": {"waves": [{"index": 1, "edges": []}]}}) + "\n", "")


def test_a_plan_is_reused_only_when_audited_for_this_task_set_and_the_spec_is_unchanged(repo):
    """The owner's rule: never redo a step that was done and whose inputs have not changed.
    A plan was DONE when an AUDIT: PASS is on record for this exact task set; UNCHANGED when
    the spec index reads REUSE. Neither alone; never the triage word; never a mechanical
    check standing in for the audit's judgement."""
    store = StoreWithChildren(3, ready=True)
    fp = mod.Sequencer("E-1", mode="auto", project=project(), store=store, cwd=str(repo)).plan_fingerprint()
    store.notes = f"ADEQUACY: ADEQUATE 2026-09-01\nAUDIT: PASS 2026-09-01 — 0 blocking, 2 filed; plan {fp}; see x"
    folder = repo / "docs" / "proposed" / "E-1-widgets-epic"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "design.md").write_text("# Design\n")
    r = Runner(**{"spec-index-status.sh": (0, "REUSE — nothing cited has moved\n", ""), "tk.sh validate": VALIDATE_CLEAN})
    d = results_for()
    text, code = seq(repo, r, d, store=store).run()
    assert code == 0, text
    assert [a for a, _ in d.seen] == [], "survey, design and plan all reused: no dispatch at all"
    assert "AUDIT: PASS on record for this exact task set" in text and "apply-plan.sh" not in r.keys()
    assert any("PLAN: reused" in c[4] for c in r.calls if key(c) == "tk.sh update" and "--append-notes" in c)


def test_a_plan_is_not_reused_when_never_audited_or_the_tasks_changed_or_the_spec_moved(repo):
    store = StoreWithChildren(3, ready=True)
    fp = mod.Sequencer("E-1", mode="auto", project=project(), store=store, cwd=str(repo)).plan_fingerprint()
    folder = repo / "docs" / "proposed" / "E-1-widgets-epic"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "design.md").write_text("# Design\n")
    reuse = {"spec-index-status.sh": (0, "REUSE — nothing cited has moved\n", ""), "tk.sh validate": VALIDATE_CLEAN}
    # never audited
    store.notes = "ADEQUACY: ADEQUATE 2026-09-01"
    d = results_for(**{"planner": PLAN, "analyst": "VERDICT: PASS\n"})
    text, code = seq(repo, Runner(**reuse), d, store=store).run()
    assert "planner" in [a for a, _ in d.seen] and "never been audited" in text
    # audited, but the tasks changed since
    store.notes = "ADEQUACY: ADEQUATE 2026-09-01\nAUDIT: PASS 2026-09-01 — plan 0000000000; see x"
    d = results_for(**{"planner": PLAN, "analyst": "VERDICT: PASS\n"})
    text, code = seq(repo, Runner(**reuse), d, store=store).run()
    assert "planner" in [a for a, _ in d.seen] and "the tasks changed since" in text
    # audited for this set, but the spec moved (DELTA → the survey re-runs, the plan's inputs moved)
    store.notes = f"ADEQUACY: ADEQUATE 2026-09-01\nAUDIT: PASS 2026-09-01 — plan {fp}; see x"
    d = results_for(**{"analyst-survey": SURVEY, "architect": DESIGN, "planner": PLAN, "analyst": "VERDICT: PASS\n"})
    text, code = seq(repo, Runner(**{"spec-index-status.sh": (0, "DELTA\n- docs/features/widgets/README.md\n", ""), "tk.sh validate": VALIDATE_CLEAN}), d, store=store).run()
    assert "planner" in [a for a, _ in d.seen] and "the plan's inputs moved" in text
    # and the audit note written by a fresh audit carries the fingerprint for next time


def test_the_audit_note_carries_the_plan_fingerprint(repo):
    store = StoreWithChildren(2, ready=True)
    r = Runner(**{"tk.sh validate": VALIDATE_CLEAN})
    d = results_for(**{"analyst-survey": SURVEY, "architect": DESIGN, "planner": PLAN, "analyst": "VERDICT: PASS\n"})
    s = seq(repo, r, d, store=store)
    s.run()
    fp = s.plan_fingerprint()
    audit_notes = [c[4] for c in r.calls if key(c) == "tk.sh update" and "--append-notes" in c and c[4].startswith("AUDIT: PASS")]
    assert audit_notes and f"plan {fp}" in audit_notes[0]


def test_a_staged_design_is_reused_when_the_spec_index_reads_reuse_and_not_otherwise(repo):
    folder = repo / "docs" / "proposed" / "E-1-widgets-epic"
    folder.mkdir(parents=True)
    (folder / "design.md").write_text("# Design\n\nARCHITECTURE: as staged\n")
    (folder / "spec-index.md").write_text("---\ngenerated_sha: abc\n---\n")
    r = Runner(**{"spec-index-status.sh": (0, "REUSE — nothing cited has moved\n", ""), "tk.sh validate": VALIDATE_CLEAN})
    d = results_for(**{"planner": PLAN, "analyst": "VERDICT: PASS\n"})
    s = seq(repo, r, d, store=StoreWithChildren(3, ready=True, notes="ADEQUACY: ADEQUATE 2026-09-01"))
    s.state.data["triage"] = "PARTIAL"
    text, code = s.run()
    assert code == 0, text
    assert "architect" not in [a for a, _ in d.seen] and "reused — " in text and "spec index reads REUSE" in text
    assert any("ARCHITECTURE: reused" in c[4] for c in r.calls if key(c) == "tk.sh update" and "--append-notes" in c)
    # REBUILD: the design is dispatched even though a file is staged
    r = Runner(**{"tk.sh validate": VALIDATE_CLEAN})
    d = results_for(**{"analyst-survey": SURVEY, "architect": DESIGN, "planner": PLAN, "analyst": "VERDICT: PASS\n"})
    s = seq(repo, r, d, store=StoreWithChildren(3, ready=True))
    s.state.data["triage"] = "PARTIAL"
    s.run()
    assert "architect" in [a for a, _ in d.seen]


class SimpleStore(StoreWithChildren):
    """Tasks that name new files under an untriggered area: the card reads simple."""

    def __init__(self, n=2, notes=""):
        super().__init__(n, ready=True, notes=notes)
        self.kids = [Task(id=k.id, type=k.type, status=k.status, title=k.title, parent=k.parent, description=f"Add src/new{i}.py\nSURFACE: none", acceptance=k.acceptance) for i, k in enumerate(self.kids, 1)]


def test_the_plan_tiers_lever_tiers_from_the_card_and_never_moves_the_planner(repo, monkeypatch):
    """Off: every dispatch at its declared tier. On: the architect at strong unless the
    surface is flagged (it escalates itself), the audit at worker only when the surface
    reads simple. The planner is never moved — it writes the DAG."""
    (repo / "src").mkdir(exist_ok=True)
    tiers = []

    def dispatch(agent, pfile, out, tier=None):
        tiers.append((agent, tier))
        return results_for(**{"analyst-survey": SURVEY, "architect": DESIGN, "planner": PLAN, "analyst": "VERDICT: PASS\n"})(agent, pfile, out)

    def run(triage, store):
        r = Runner(**{"tk.sh validate": VALIDATE_CLEAN})
        s = mod.Sequencer("E-1", mode="auto", project=project(), runner=r, cwd=str(repo), dispatch_fn=dispatch, store=store)
        s.state.data["triage"] = triage
        s.state.data["done"] = []
        s.run()
        out = dict(tiers)
        tiers.clear()
        return out

    monkeypatch.delenv("MAD_HARNESS_PLAN_TIERS", raising=False)
    assert all(t is None for t in run("READY", SimpleStore(2)).values())
    monkeypatch.setenv("MAD_HARNESS_PLAN_TIERS", "1")
    by = run("READY", SimpleStore(2))
    assert by["architect"] == "strong" and by["analyst"] == "worker" and by["planner"] is None, by
    by = run("PARTIAL", SimpleStore(40))
    assert by["architect"] == "strong" and by["analyst"] == "worker", "forty new files in an untriggered area is still simple"
    by = run("READY", StoreWithChildren(2, ready=True))
    assert by["architect"] == "strong" and by["analyst"] is None, "tasks naming no paths: the architect still at strong (it escalates itself); the audit at its declared tier"
    flagged = SimpleStore(2)
    flagged.kids = [Task(id=k.id, type=k.type, status=k.status, title=k.title, parent=k.parent, description="Edit harness/models/dispatch.py\nSURFACE: none", acceptance=k.acceptance) for k in flagged.kids]
    (repo / "harness" / "models").mkdir(parents=True, exist_ok=True)
    (repo / "harness" / "models" / "dispatch.py").write_text("x = 1\n")
    by = run("READY", flagged)
    assert by["architect"] is None and by["analyst"] is None, "a triggered area: the declared tiers"


def test_a_lighter_stage_may_escalate_once_to_its_declared_tier_and_the_reason_travels(repo, monkeypatch):
    (repo / "src").mkdir(exist_ok=True)
    monkeypatch.setenv("MAD_HARNESS_PLAN_TIERS", "1")
    calls = []

    def dispatch(agent, pfile, out, tier=None):
        calls.append((agent, tier, pfile.read_text()))
        if agent == "architect" and tier == "strong":
            out.write_text("ADEQUACY: ESCALATE — the phone rule touches a contract three callers depend on\n")
            return Raw(0, f"full: {out}", "")
        if agent == "analyst" and tier == "worker":
            out.write_text("VERDICT: ESCALATE — the plan re-scopes an ADR\n")
            return Raw(0, f"full: {out}", "")
        return results_for(**{"analyst-survey": SURVEY, "architect": DESIGN, "planner": PLAN, "analyst": "VERDICT: PASS\n"})(agent, pfile, out)

    r = Runner(**{"tk.sh validate": VALIDATE_CLEAN})
    s = mod.Sequencer("E-1", mode="auto", project=project(), runner=r, cwd=str(repo), dispatch_fn=dispatch, store=SimpleStore(2))
    s.state.data["triage"] = "READY"
    text, code = s.run()
    assert code == 0, text
    arch = [(t, p) for a, t, p in calls if a == "architect"]
    assert [t for t, _ in arch] == ["strong", None], "once at strong, once escalated to the declared tier"
    assert "ESCALATED from a lighter tier, which said: ADEQUACY: ESCALATE — the phone rule" in arch[1][1]
    assert "TIER: you are running at `strong`" in arch[0][1] and "TIER:" not in arch[1][1]
    aud = [(t, p) for a, t, p in calls if a == "analyst"]
    assert [t for t, _ in aud] == ["worker", None] and "ESCALATED from tier worker" in aud[1][1]
    notes = [c[4] for c in r.calls if key(c) == "tk.sh update" and "--append-notes" in c]
    assert any(n.startswith("ESCALATED: architect strong") for n in notes) and any(n.startswith("ESCALATED: audit worker") for n in notes)
    assert "ESCALATE at tier strong" in text and "ESCALATE at tier worker" in text


def test_escalate_at_the_declared_tier_parks_rather_than_looping(repo, monkeypatch):
    monkeypatch.delenv("MAD_HARNESS_PLAN_TIERS", raising=False)
    r = Runner()
    d = results_for(**{"analyst-survey": SURVEY, "architect": "ADEQUACY: ESCALATE — beyond me\n"})
    text, code = seq(repo, r, d).run()
    assert code == 4 and "nothing above it" in text and "tk.sh park" in r.keys()
    assert [a for a, _ in d.seen].count("architect") == 1


def test_the_audit_is_handed_the_rendered_view_and_told_not_to_loop(repo):
    r = Runner()
    d = results_for(**{"analyst-survey": SURVEY, "architect": DESIGN, "planner": PLAN, "analyst": "VERDICT: PASS\n"})
    s = seq(repo, r, d)
    s.state.data["triage"] = "PARTIAL"
    s.run()
    audit_prompt = next(t for a, t in d.seen if a == "analyst")
    assert "tasks.md" in audit_prompt and "never in a shell loop" in audit_prompt


def test_every_stage_prompt_states_the_size_of_the_epic_and_asks_for_proportion(repo):
    """Measured: for an epic whose whole source was 239 words the architect wrote 1,621
    words, the planner 3,876, the audit 17k tokens — each stage's time was its output and
    the output was the template's shape. The sequencer knows the task count; it says so."""
    r = Runner(**{"tk.sh validate": VALIDATE_CLEAN})
    d = results_for(**{"analyst-survey": SURVEY, "architect": DESIGN, "planner": PLAN, "analyst": "VERDICT: PASS\n"})
    s = seq(repo, r, d, store=StoreWithChildren(3, ready=False))
    s.state.data["triage"] = "READY"
    s.run()
    prompts = dict(d.seen)
    assert "SIZE: this epic has 3 open task(s)" in prompts["architect"] and "a paragraph per task at most" in prompts["architect"]
    assert "SIZE: this epic has 3 open task(s)" in prompts["planner"] and "Emit only what changes" in prompts["planner"]
    assert "SIZE: the plan covers 3 open task(s)" in prompts["analyst"] and "One line per task" in prompts["analyst"]
    d2 = results_for(**{"analyst-survey": SURVEY, "architect": DESIGN, "planner": PLAN, "analyst": "VERDICT: PASS\n"})
    s2 = seq(repo, Runner(**{"tk.sh validate": VALIDATE_CLEAN}), d2, store=Store())
    s2.state.data["triage"] = "UNPLANNED"
    s2.state.data["done"] = []
    s2.run()
    assert "SIZE: this epic has no tasks yet" in dict(d2.seen)["architect"] and "a small change gets a short design" in dict(d2.seen)["architect"]


def test_every_stage_prompt_ends_with_the_one_plain_command_rule(repo):
    """Two of four surveys in the first plan-only series were refused a compound command
    and their complete results went unjudged; the lens prompts carried the rule and had
    no denials. Every §3 prompt carries it."""
    r = Runner(**{"tk.sh validate": VALIDATE_CLEAN})
    d = results_for(**{"analyst-survey": SURVEY, "architect": DESIGN, "planner": PLAN, "analyst": "VERDICT: PASS\n"})
    s = seq(repo, r, d, store=StoreWithChildren(3, ready=False))
    s.state.data["triage"] = "PARTIAL"
    s.run()
    assert len(d.seen) >= 4
    for agent, text in d.seen:
        assert "EVERY Bash call is ONE plain command" in text and "find -exec" in text, agent
