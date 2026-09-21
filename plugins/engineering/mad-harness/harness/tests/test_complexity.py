"""The complexity card — read from what the project declares, before §3 spends; deep by
default, lightened only by evidence; the model may escalate, never the reverse."""

from __future__ import annotations

import json

import pytest

from models import complexity as mod
from models.project import Area, Project
from tracker.port import Task


def project(**over):
    base = dict(
        name="P", slug="p", stacks=(),
        paths={},
        areas=(Area(path="src/auth/", label="auth", triggers=("security",)), Area(path="src/", label="app")),
        security={"paths": ["src/payments/"], "tokens": ["credential"]},
        raw={"signals": {"megafile_lines": 100}},
    )
    base.update(over)
    return Project(**base)


def task(i, text):
    return Task(id=f"E.{i}", type="task", status="open", title=f"T{i}", parent="E", description=text, acceptance="- ok\nSURFACE: none")


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "src" / "auth").mkdir(parents=True)
    (tmp_path / "src" / "payments").mkdir(parents=True)
    (tmp_path / "src" / "small.py").write_text("x = 1\n" * 10)
    (tmp_path / "src" / "big.py").write_text("y = 1\n" * 500)
    (tmp_path / "src" / "auth" / "session.py").write_text("s = 1\n")
    (tmp_path / "src" / "payments" / "charge.py").write_text("c = 1\n")
    return tmp_path


def validate_ok(edges=0):
    def runner(argv, cwd=None, capture_output=True, text=True, timeout=None, env=None):
        import subprocess

        doc = {"ok": True, "waves": [], "contention": {"waves": [{"index": 1, "edges": [{"tasks": ["a", "b"], "path": "src/small.py"}] * edges}]}}
        return subprocess.CompletedProcess(argv, 0, json.dumps(doc), "")
    return runner


def test_new_files_in_an_untriggered_area_read_simple_and_say_why(repo):
    kids = [task(1, "Add src/email.py"), task(2, "Add src/phone.py"), task(3, "Edit src/small.py to call both")]
    card = mod.compute("Normalise contacts", kids, project(), cwd=str(repo), runner=validate_ok(), epic_id="E")
    assert card.simple, card.why()
    assert card.new == ["src/email.py", "src/phone.py"] and card.existing == ["src/small.py"]
    assert card.render().startswith("COMPLEXITY: simple — 3 task(s); 2 new file(s), 1 existing (10 lines) in app")
    assert card.edges == 0


@pytest.mark.parametrize(
    "text, reason",
    [
        ("Edit src/auth/session.py", "touches a triggered area: auth"),
        ("Edit src/payments/charge.py", "security.paths `src/payments/`"),
        ("Rotate the credential in src/small.py", "security.tokens `credential`"),
        ("Edit src/big.py", "megafile: src/big.py (500 lines)"),
    ],
)
def test_anything_the_project_flagged_reads_deep_with_the_flag_named(repo, text, reason):
    card = mod.compute("", [task(1, text)], project(), cwd=str(repo), runner=validate_ok(), epic_id="E")
    assert card.flagged and not card.simple and reason in card.why(), card.why()
    assert card.render().startswith("COMPLEXITY: flagged — ")


def test_a_contention_edge_reads_deep_and_an_unreadable_validate_is_said(repo):
    card = mod.compute("", [task(1, "Edit src/small.py"), task(2, "Edit src/small.py")], project(), cwd=str(repo), runner=validate_ok(edges=1), epic_id="E")
    assert not card.simple and "1 file-contention edge" in card.why()

    def broken(argv, **kw):
        import subprocess

        return subprocess.CompletedProcess(argv, 1, "", "no tracker")
    card = mod.compute("", [task(1, "Edit src/small.py")], project(), cwd=str(repo), runner=broken, epic_id="E")
    assert card.edges is None and "contention unread" in card.notes[0]
    assert card.simple, "an unread contention is not evidence of complexity; the flags are"


def test_no_tasks_or_no_paths_is_unreadable_not_simple_and_not_flagged(repo):
    card = mod.compute("Make it better", [], project(), cwd=str(repo))
    assert card.unreadable and not card.simple and not card.flagged and card.reading == "unreadable"
    card = mod.compute("Make it better", [task(1, "Improve things generally")], project(), cwd=str(repo), runner=validate_ok(), epic_id="E")
    assert card.unreadable and not card.flagged and "name no paths" in card.why()
    assert card.render().startswith("COMPLEXITY: unreadable — ")


def test_task_count_is_shown_never_gated_on(repo):
    kids = [task(i, f"Add src/new{i}.py") for i in range(1, 41)]
    card = mod.compute("", kids, project(), cwd=str(repo), runner=validate_ok(), epic_id="E")
    assert card.simple and "40 task(s)" in card.why()
    one = mod.compute("", [task(1, "Edit src/auth/session.py")], project(), cwd=str(repo), runner=validate_ok(), epic_id="E")
    assert not one.simple, "one task on the auth adapter is not simple"
