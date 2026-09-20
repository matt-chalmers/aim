"""§3 as a sequencer: the routing per verdict, the two interactive stops, the second-FAIL
park, the ABSENT path, the fresh planner on a revision, and the staging writes. The five
dispatches are faked by result text; the tracker and the scripts by an injected runner."""

from __future__ import annotations

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
    return Project(name="T", slug="t", stacks=(), paths={"proposed": "docs/proposed", "adrs": "docs/decisions"}, areas=(), security={}, raw={"lanes": {"backend": {"cap": 4}}})


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
    assert r.calls[-1][0].endswith("apply-plan.sh") and "--render" in r.calls[-1]


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
