"""/halt as one call per form: assess writes nothing; pause parks and reports a resume
point per claim; release preserves BEFORE it releases and never removes before preserve;
an alive slot holder is never forced; every write ends with autosync restored."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from models import halt as mod
from models.steps import FAIL

BACKEND = json.dumps({"name": "beads", "tracked_export": True, "owned_paths": [".beads/"], "export_path": ".beads/issues.jsonl"})
CLAIMS = json.dumps([{"task": "T-1", "holder": "w1", "host": "mac", "pid": 1, "age_s": 600, "alive": False, "stale": True}])
FREE = json.dumps({"free": True, "holder": "", "stale": False})
STALE = json.dumps({"free": False, "holder": "w9", "stale": True})
ALIVE = json.dumps({"free": False, "holder": "w9", "stale": False})
RP_REATTACH = json.dumps({"task": "T-1", "state": "REATTACH", "branch": "harness-w1-T-1", "worktree": "/wt/harness-w1-T-1", "dirty": True})
RP_FRESH = json.dumps({"task": "T-1", "state": "FRESH", "branch": None, "worktree": None})
PRESERVED = "  preserved harness-w1-T-1 → .harness/halted-2026-09-20/harness-w1-T-1  (uncommitted: yes, commits ahead: 1)\n  1 worktree(s) preserved under .harness/halted-2026-09-20.\n"


def key(argv):
    name = Path(argv[0]).name
    if name == "git":
        return f"git {argv[1]}" + (f" {argv[2]}" if argv[1] == "worktree" else "")
    return f"{name} {argv[1]}" if name == "tk.sh" else name


class Runner:
    def __init__(self, **answers):
        self.answers = {
            "git status": (0, "", ""),
            "git rev-list": (0, "1\n", ""),
            "git log": (0, "abc feat: x (T-1)\n", ""),
            "git worktree prune": (0, "", ""),
            "git worktree remove": (0, "", ""),
            "git rev-parse": (1, "", ""),
            "git pull": (0, "", ""),
            "git push": (0, "", ""),
            "git add": (0, "", ""),
            "git diff": (1, "", ""),
            "git commit": (0, "[main abc] chore\n", ""),
            "tk.sh claims": (0, CLAIMS, ""),
            "tk.sh slot-check": (0, FREE, ""),
            "tk.sh slot-release": (0, "", ""),
            "tk.sh backend": (0, BACKEND, ""),
            "tk.sh park": (0, "parked E-1 — gate G-1, status blocked\n", ""),
            "tk.sh release": (0, "released\n", ""),
            "tk.sh update": (0, "", ""),
            "tk.sh export": (0, "", ""),
            "tk.sh autosync": (0, "", ""),
            "resume-point.sh": (0, RP_REATTACH, ""),
            "preserve-worktrees.sh": (0, PRESERVED, ""),
        }
        self.answers.update({k.replace("_", " "): v for k, v in answers.items()})
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        a = self.answers[key(argv)]
        if isinstance(a, BaseException):
            raise a
        rc, out, err = a
        if callable(out):
            out = out(argv)
        return subprocess.CompletedProcess(argv, rc, out, err)

    def keys(self):
        return [key(c) for c in self.calls]

    def wrote(self):
        return [k for k in self.keys() if k in WRITES]


WRITES = {"tk.sh park", "tk.sh release", "tk.sh update", "tk.sh slot-release", "git worktree prune", "git worktree remove", "tk.sh export", "git add", "git commit", "git pull", "git push", "tk.sh autosync", "preserve-worktrees.sh"}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    r.mkdir()
    (r / "harness.yaml").write_text("name: T\nslug: t\nareas: []\npaths: {proposed: docs/proposed}\n")
    monkeypatch.setenv("MAD_HARNESS_REPO", str(r))
    monkeypatch.setattr("models.resolve.REPO", r)
    monkeypatch.setattr("models.project.REPO", r)
    monkeypatch.setattr("models.project.PROJECT_FILE", r / "harness.yaml")
    monkeypatch.setattr(mod, "REPO", r)
    monkeypatch.setattr("models.tracker_sync.REPO", r)
    monkeypatch.setattr("models.resume._worktrees", lambda repo: {})
    monkeypatch.setattr("models.resume.main_branch", lambda repo: "main")
    return r


def project():
    from models.project import load

    return load()


def test_assess_writes_nothing_and_reads_the_claims_not_the_status(repo):
    r = Runner()
    results, facts = mod.assess(r, str(repo))
    assert r.wrote() == []
    assert facts["claims"][0]["task"] == "T-1"
    claims = next(x for x in results if x.name == "tk.sh claims")
    assert "the authority on what is held" in claims.detail and "T-1  held by w1" in claims.detail
    assert r.calls[r.keys().index("tk.sh claims")][1:] == ["claims", "--json"]


def test_pause_parks_and_reports_a_resume_point_per_claim(repo):
    r = Runner()
    results, code = mod.pause("E-1", push=True, dry_run=False, project=project(), runner=r, cwd=str(repo))
    assert code == 0
    park = r.calls[r.keys().index("tk.sh park")]
    assert park[1:] == ["park", "E-1", "--reason", park[-1]] and park[-1].startswith("paused 20")
    assert "resume-point.sh" in r.keys()
    assert "tk.sh release" not in r.keys(), "pause keeps the claims — they ARE the pause"
    assert r.keys()[-1] == "tk.sh autosync", "every write ends with what pre-flight disabled restored"


def test_release_preserves_before_it_releases_and_never_removes_before_preserve(repo):
    r = Runner()
    results, code = mod.release([], epic="E-1", drop_uncommitted=True, push=True, dry_run=False, project=project(), runner=r, cwd=str(repo))
    assert code == 0, "\n".join(x.line() for x in results)
    ks = r.keys()
    assert ks.index("preserve-worktrees.sh") < ks.index("tk.sh release") < ks.index("tk.sh update") < ks.index("git worktree remove"), "preserve → release → note → remove"
    note = r.calls[ks.index("tk.sh update")]
    assert note[1:4] == ["update", "T-1", "--append-notes"] and "preserved at .harness/halted-2026-09-20/harness-w1-T-1" in note[4] and "REATTACH" in note[4]
    assert r.calls[ks.index("git worktree remove")][1:] == ["worktree", "remove", "--force", "/wt/harness-w1-T-1"]


def test_a_failed_preserve_stops_everything(repo):
    r = Runner(**{"preserve-worktrees.sh": (2, "", "cannot determine the default branch\n")})
    results, code = mod.release([], epic="E-1", drop_uncommitted=True, push=True, dry_run=False, project=project(), runner=r, cwd=str(repo))
    assert code == 2 and "STOPPED — nothing released and nothing removed" in "\n".join(x.detail for x in results)
    assert "tk.sh release" not in r.keys() and "git worktree remove" not in r.keys() and "tk.sh autosync" not in r.keys()


def test_reattach_is_kept_unless_drop_uncommitted(repo):
    r = Runner()
    results, code = mod.release([], epic="E-1", drop_uncommitted=False, push=True, dry_run=False, project=project(), runner=r, cwd=str(repo))
    assert code == 0 and "git worktree remove" not in r.keys()
    assert any("KEPT with its uncommitted work" in x.detail for x in results)


def test_a_worktree_is_never_removed_when_preserve_did_not_report_its_branch(repo):
    r = Runner(**{"preserve-worktrees.sh": (0, "  nothing to preserve — no worker worktree holds uncommitted or unmerged work\n", "")})
    results, code = mod.release([], epic="E-1", drop_uncommitted=True, push=True, dry_run=False, project=project(), runner=r, cwd=str(repo))
    assert code == 1 and "git worktree remove" not in r.keys()
    assert any("NOT removed" in x.detail and "only copy" in x.detail for x in results)


def test_an_alive_slot_holder_is_never_forced_and_a_stale_one_is_released(repo):
    r = Runner(**{"tk.sh slot-check": (0, ALIVE, "")})
    results, code = mod.pause("E-1", push=True, dry_run=False, project=project(), runner=r, cwd=str(repo))
    assert code == 1 and "tk.sh slot-release" not in r.keys()
    assert any(x.status == FAIL and "ALIVE — not released" in x.detail for x in results)

    r = Runner(**{"tk.sh slot-check": (0, STALE, "")})
    results, code = mod.pause("E-1", push=True, dry_run=False, project=project(), runner=r, cwd=str(repo))
    assert code == 0 and r.calls[r.keys().index("tk.sh slot-release")][1:] == ["slot-release", "--force"]


def test_always_restores_autosync_and_syncs_with_the_halt_message(repo):
    r = Runner()
    mod.pause("E-1", push=True, dry_run=False, project=project(), runner=r, cwd=str(repo))
    assert r.calls[r.keys().index("git commit")][-1] == "chore(tracker): halt E-1 — pause"
    assert "git worktree prune" in r.keys() and r.calls[r.keys().index("tk.sh autosync")][1:] == ["autosync", "on"]


def test_dry_run_reads_and_writes_nothing(repo):
    r = Runner()
    results, code = mod.release([], epic="E-1", drop_uncommitted=True, push=True, dry_run=True, project=project(), runner=r, cwd=str(repo))
    assert code == 0 and r.wrote() == []
    assert any("would preserve" in x.detail for x in results) and any("would release" in x.detail for x in results)


def test_a_failed_park_stops_before_the_tail_and_says_the_command(repo):
    r = Runner(**{"tk.sh park": (1, "", "park: E-1 is already blocked (gate G-1)\n")})
    results, code = mod.pause("E-1", push=True, dry_run=False, project=project(), runner=r, cwd=str(repo))
    assert code == 1 and "already blocked" in "\n".join(x.detail for x in results) and "tk.sh autosync" not in r.keys()


def test_release_names_the_tasks_given_rather_than_every_claim(repo):
    r = Runner(**{"resume-point.sh": (0, RP_FRESH, "")})
    mod.release(["T-7"], epic=None, drop_uncommitted=False, push=False, dry_run=False, project=project(), runner=r, cwd=str(repo))
    rel = [c for c in r.calls if key(c) == "tk.sh release"]
    assert [c[2] for c in rel] == ["T-7"]
    assert "git push" not in r.keys()


def test_main_and_wrapper(repo, monkeypatch, capsys):
    r = Runner()
    monkeypatch.setattr("models.steps.subprocess.run", r)
    assert mod.main(["assess"]) == 0
    out = capsys.readouterr().out
    assert "halt assess" in out and "Nothing changed" in out and r.wrote() == []
    sh = Path(mod.HARNESS) / "swarm" / "halt.sh"
    assert sh.exists() and os.access(sh, os.X_OK) and "python -m models.halt" in sh.read_text()
