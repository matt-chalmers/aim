"""The writer's prompt assembled by the dispatcher, the claim taken before the spawn, and
the commit inside the mutex — three things the prose had the orchestrator and the worker
do by hand, N times per wave."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from models import commit as cm
from models import worker_prompt as wp
from tracker.port import ClaimResult, Task


class Store:
    def __init__(self, task):
        self._t = task

    def show(self, tid):
        return self._t if tid == self._t.id else None


def a_task(**over):
    base = dict(
        id="T-7", type="task", status="open", title="Add rate limiting to the login endpoint", parent="E-1",
        description="Limit POST /login to 5/min per user. Touches src/auth/login.py and tests/auth/test_login.py.\nSURFACE: authorization on the login endpoint; ADR-0007\nAUTHORITATIVE SPEC: docs/features/auth.md §3",
        acceptance="- AC1 429 after the 5th attempt\n- AC2 the window resets", notes="", labels=("backend",),
    )
    base.update(over)
    return Task(**base)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    (r / "docs" / "proposed" / "E-1-auth").mkdir(parents=True)
    (r / "docs" / "proposed" / "E-1-auth" / "spec-index.md").write_text(
        "# SPEC INDEX\n\n- docs/features/auth.md §3 — login limits (AUTHORITATIVE)\n- ADR-0007 — rate limits are per user, not per IP\n- docs/features/billing.md — unrelated\n"
    )
    (r / "harness.yaml").write_text("name: T\nslug: t\nareas: []\nbeads: {prefix: E}\npaths: {proposed: docs/proposed}\n")
    monkeypatch.setenv("MAD_HARNESS_REPO", str(r))
    monkeypatch.setattr("models.resolve.REPO", r)
    monkeypatch.setattr("models.project.REPO", r)
    monkeypatch.setattr("models.project.PROJECT_FILE", r / "harness.yaml")
    monkeypatch.setattr(wp, "REPO", r)
    return r


def project():
    from models.project import load

    return load()


# --- the prompt ------------------------------------------------------------------------------


def test_the_record_is_verbatim_and_the_spec_slice_is_pointers_only(repo):
    memories = ["auth-rate-limits: the window is per user, measured 2026-08 — see login.py", "billing-rounding: unrelated"]
    text = wp.build("T-7", lane="backend", worker=2, store=Store(a_task()), memories=memories, project=project())
    assert "T-7  open  task  Add rate limiting" in text and "--- acceptance criteria ---" in text and "AC1 429" in text
    assert "docs/features/auth.md §3 — login limits (AUTHORITATIVE)" in text and "ADR-0007 — rate limits" in text
    assert "billing.md" not in text, "only the entries the SURFACE/AUTHORITATIVE lines point at"
    assert "OPEN the docs, never build from a paraphrase" in text
    assert "auth-rate-limits:" in text and "billing-rounding" not in text
    assert "commit.sh T-7 -m" in text and "run.sh --lane backend test_scoped" in text and "worker 2" in text


def test_no_index_is_said_not_omitted(repo, tmp_path):
    (repo / "docs" / "proposed" / "E-1-auth" / "spec-index.md").unlink()
    text = wp.build("T-7", lane=None, worker=1, store=Store(a_task()), memories=[], project=project())
    assert "No SPEC INDEX for epic E-1" in text
    text = wp.build("T-7", lane=None, worker=1, store=Store(a_task(parent=None)), memories=[], project=project())
    assert "No SPEC INDEX: the task has no parent epic" in text
    assert "No index entry matched" in text


def test_ban_list_and_return_contract_are_not_duplicated_from_doctrine(repo):
    """Those ride in the worker's system prompt (`worker-protocol`) since 0.10.18; a copy
    in the message drifts."""
    text = wp.build("T-7", lane="backend", worker=1, store=Store(a_task()), memories=[], project=project())
    assert "ten lines" not in text.lower() and "Resource ban" not in text and "NEEDS-SERIAL-LANE" not in text


def test_a_fidelity_task_carries_the_auditors_defect_list(repo):
    t = a_task(labels=("fidelity",), notes="FIDELITY AUDIT: …\nDEFECTS:\n- heading 20px, handover 24px\n- ring 1px, handover 2px\n")
    text = wp.build("T-7", lane="frontend", worker=1, store=Store(t), memories=[], project=project())
    assert "measured defect list" in text and "heading 20px, handover 24px" in text
    t = a_task(labels=("fidelity",), notes="")
    assert "carries no `DEFECTS:`" in wp.build("T-7", lane="frontend", worker=1, store=Store(t), memories=[], project=project())


def test_extra_is_appended_under_its_own_heading(repo):
    text = wp.build("T-7", lane="backend", worker=1, store=Store(a_task()), memories=[], project=project(), extra="Trap: the fixture at tests/conftest.py resets the clock.")
    assert "## From the orchestrator" in text and "resets the clock" in text


def test_an_unknown_task_is_a_lookup_error(repo):
    with pytest.raises(LookupError):
        wp.build("T-9", lane=None, worker=1, store=Store(a_task()), memories=[], project=project())


# --- claim before spawn ------------------------------------------------------------------------


def test_claim_before_spawn_exits_without_dispatching_on_a_lost_claim(monkeypatch):
    from models import dispatch as D

    class Co:
        def try_claim(self, task, actor):
            return ClaimResult(held=False, holder="swarm-w3")

    import tracker

    monkeypatch.setattr(tracker, "coordination", lambda: Co())
    why = D.claim_first("T-7", 1)
    assert why and "already claimed by swarm-w3" in why and "fixed cost was not paid" in why


def test_claim_before_spawn_holds_under_the_workers_actor_and_is_reentrant(monkeypatch):
    from models import dispatch as D

    seen = {}

    class Co:
        def try_claim(self, task, actor):
            seen["actor"] = actor
            return ClaimResult(held=True, holder=actor, reentrant=True)

    import tracker

    monkeypatch.setattr(tracker, "coordination", lambda: Co())
    assert D.claim_first("T-7", 4) is None and seen["actor"] == "swarm-w4", "the actor the worker's .swarm-env exports, so its own claim is re-entrant"


def test_a_tracker_that_cannot_answer_does_not_stop_the_dispatch(monkeypatch, capsys):
    import tracker
    from models import dispatch as D

    def boom():
        raise RuntimeError("no db")

    monkeypatch.setattr(tracker, "coordination", boom)
    assert D.claim_first("T-7", 1) is None
    assert "the worker claims for itself" in capsys.readouterr().err


def test_dispatch_refuses_task_prompt_without_task(capsys):
    from models import dispatch as D

    assert D.main(["fullstack-engineer", "--task-prompt"]) == 2
    assert "--task-prompt needs --task" in capsys.readouterr().err


# --- the commit --------------------------------------------------------------------------------


def key(argv):
    name = Path(argv[0]).name
    if name == "git":
        return f"git {argv[1]}"
    return f"{name} {argv[1]}" if name == "tk.sh" else name


class Runner:
    def __init__(self, **answers):
        self.answers = {
            "git status": (0, " M src/auth/login.py\n?? tests/auth/test_login.py\n?? .swarm-env\n", ""),
            "tk.sh slot-acquire": (0, "", ""),
            "tk.sh slot-release": (0, "", ""),
            "git add": (0, "", ""),
            "git commit": (0, "[harness-w1-T-7 abcdef123456] feat(auth): rate limit (T-7)\n", ""),
            "git rev-parse": (0, "abcdef123456\n", ""),
        }
        self.answers.update({k.replace("_", " "): v for k, v in answers.items()})
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        a = self.answers[key(argv)]
        if isinstance(a, BaseException):
            raise a
        rc, out, err = a
        return subprocess.CompletedProcess(argv, rc, out, err)

    def keys(self):
        return [key(c) for c in self.calls]


def test_the_commit_takes_the_slot_stages_only_the_named_paths_and_releases_it(monkeypatch):
    monkeypatch.setenv("TRACKER_ACTOR", "swarm-w1")
    r = Runner()
    text, code = cm.run("T-7", "feat(auth): rate limit (T-7)", ["src/auth/login.py", "tests/auth/test_login.py"], runner=r, cwd="/wt")
    assert code == 0 and "COMMITTED" in text
    assert r.keys() == ["git status", "tk.sh slot-acquire", "git add", "git commit", "git rev-parse", "tk.sh slot-release"]
    assert r.calls[1][1:] == ["slot-acquire", "--holder", "swarm-w1"]
    assert r.calls[2] == ["git", "add", "--", "src/auth/login.py", "tests/auth/test_login.py"], "explicit paths, never -A"


def test_a_foreign_staged_path_is_refused_before_the_slot_is_taken():
    r = Runner(git_status=(0, " M src/auth/login.py\n M src/billing/x.py\n", ""))
    text, code = cm.run("T-7", "feat (T-7)", ["src/auth/login.py"], runner=r, cwd="/wt")
    assert code == 1 and "contaminated index" in text and "src/billing/x.py" in text
    assert "tk.sh slot-acquire" not in r.keys() and "git add" not in r.keys()


def test_harness_residue_is_not_contamination():
    r = Runner(git_status=(0, " M src/auth/login.py\n?? .swarm-env\n?? __pycache__/x.pyc\n?? .harness/run/out/x.log\n", ""))
    text, code = cm.run("T-7", "feat (T-7)", ["src/auth/login.py"], runner=r, cwd="/wt")
    assert code == 0


def test_the_tracked_export_is_refused():
    r = Runner(git_status=(0, " M .beads/issues.jsonl\n M src/x.py\n", ""))
    text, code = cm.run("T-7", "feat (T-7)", ["src/x.py", ".beads/issues.jsonl"], runner=r, cwd="/wt", export=".beads/issues.jsonl")
    assert code == 1 and "never commit the tracker's export" in text and "tk.sh slot-acquire" not in r.keys()


def test_a_staged_export_the_worker_never_touched_is_unstaged_not_contamination():
    """beads' hooks stage the export on every write; refusing `M  .beads/issues.jsonl` as a
    contaminated index — with "never reset" — left the worker unable to commit at all."""
    r = Runner(**{"git status": (0, "M  .beads/issues.jsonl\n M src/x.py\n", ""), "git reset": (0, "", "")})
    text, code = cm.run("T-7", "feat (T-7)", ["src/x.py"], runner=r, cwd="/wt", export=".beads/issues.jsonl")
    assert code == 0, text
    reset = next(c for c in r.calls if key(c) == "git reset")
    assert reset[-1] == ".beads/issues.jsonl" and "unstaged, not yours to commit" in text
    add = next(c for c in r.calls if key(c) == "git add")
    assert ".beads/issues.jsonl" not in add and r.keys().index("git reset") < r.keys().index("git add")


def test_a_named_path_with_no_change_is_refused():
    r = Runner(git_status=(0, " M src/x.py\n", ""))
    text, code = cm.run("T-7", "feat (T-7)", ["src/x.py", "src/y.py"], runner=r, cwd="/wt")
    assert code == 1 and "no change to commit" in text and "src/y.py" in text


def test_the_slot_is_released_even_when_the_commit_fails():
    r = Runner(git_status=(0, " M src/x.py\n", ""), git_commit=(1, "", "nothing to commit\n"))
    text, code = cm.run("T-7", "feat (T-7)", ["src/x.py"], runner=r, cwd="/wt")
    assert code == 1 and r.keys()[-1] == "tk.sh slot-release" and "NOT COMMITTED" in text


def test_a_held_slot_is_reported_and_nothing_staged():
    r = Runner(git_status=(0, " M src/x.py\n", ""), **{"tk.sh slot-acquire": (1, "", "held\n")})
    text, code = cm.run("T-7", "feat (T-7)", ["src/x.py"], runner=r, cwd="/wt")
    assert code == 1 and "a sibling is committing" in text and "git add" not in r.keys()


def test_the_message_must_name_the_task(capsys):
    assert cm.main(["T-7", "-m", "feat: thing", "src/x.py"]) == 2
    assert "must name the task id" in capsys.readouterr().err


def test_the_wrappers_are_executable_and_run_the_modules():
    root = Path(wp.__file__).resolve().parent.parent / "swarm"
    for n, m in (("commit.sh", "commit"), ("worker-prompt.sh", "worker_prompt")):
        sh = root / n
        assert sh.exists() and os.access(sh, os.X_OK) and f"python -m models.{m}" in sh.read_text(), n


def test_the_worker_is_told_never_to_close_its_task(repo):
    """Measured: a worker closed its own task after committing; the orchestrator reopened
    it as a protocol breach — and the prompt had told it to. The lens gate judges, the
    orchestrator closes."""
    text = wp.build("T-7", lane="backend", worker=2, store=Store(a_task()), memories=[], project=project())
    assert "NEVER `tk.sh close`" in text
    assert "Close with `tk.sh close" not in text
