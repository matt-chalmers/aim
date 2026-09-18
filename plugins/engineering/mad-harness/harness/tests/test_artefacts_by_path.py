"""Artefacts travel by PATH, never through the orchestrator's context.

Measured over one field campaign: the orchestrator ran 237 requests with its context
growing 55k -> 920k tokens (~380k on average), so every tool call it makes re-reads
~$0.11-0.17 of context — ~6x what the same call costs a worker. 35% of that context was
injected text, and the four largest injections were subagent results of 45k, 43k, 36k
and 23k chars, each arriving twice. Two seams close it: `dispatch.sh` keeps the whole
result on disk and prints a digest plus the path, and `tk.sh note --file` puts a file's
text on a record without the caller reading it. Nothing here invokes a model.
"""

from __future__ import annotations

import pytest

import tracker
from models import dispatch as mod
from models.dispatch import EXIT_BUDGET
from tracker import cli
from tracker.mdfiles import MdTaskStore

LONG = "\n".join(f"line {i:03d} of the design" for i in range(1, 201))
RESULT = {
    "subtype": "success",
    "is_error": False,
    "result": LONG,
    "total_cost_usd": 0.5,
    "num_turns": 9,
    "duration_ms": 1000,
    "session_id": "s-1",
    "usage": {"input_tokens": 1, "output_tokens": 2},
    "permission_denials": [],
}
KILL = {
    **RESULT,
    "subtype": "error_max_budget_usd",
    "is_error": True,
    "result": "Reached maximum budget ($1.5)",
    "terminal_reason": "max_budget_usd",
    "transcript": ["Reading the design", '[tool] Read {"file_path": "docs/x.md"}'],
}


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    """No sandbox check, no telemetry, and every file under tmp_path — never the tree."""
    monkeypatch.setattr(mod, "require_sandbox", lambda: None)
    monkeypatch.setattr(mod, "record", lambda *a, **k: True)
    monkeypatch.setattr(mod, "RESULT_DIR", tmp_path / "out")
    monkeypatch.setattr(mod, "REPO", tmp_path)


def _dispatch(monkeypatch, tmp_path, payload, *extra):
    monkeypatch.setattr(mod, "_run_sdk", lambda *a, **k: payload)
    pf = tmp_path / "p.txt"
    pf.write_text("do the thing")
    return mod.main(["verifier", "--prompt-file", str(pf), "--task", "T-1", *extra])


def _path_line(out: str) -> str:
    [line] = [ln for ln in out.splitlines() if "full: " in ln]
    return line


# --- dispatch.sh: the whole result on disk, a digest on stdout ---------------------------


def test_every_dispatch_keeps_its_whole_result_on_disk_and_prints_the_path(monkeypatch, tmp_path, capsys):
    rc = _dispatch(monkeypatch, tmp_path, RESULT)
    out = capsys.readouterr().out
    assert rc == 0
    [kept] = list((tmp_path / "out").glob("dispatch-verifier-T-1-*.md"))
    assert kept.read_text() == LONG + "\n", "the file carries the whole text, not a digest"
    assert out.splitlines()[-1] == f"full: {kept}"


def test_without_digest_stdout_is_todays_full_print_plus_one_path_line(monkeypatch, tmp_path, capsys):
    """The default must not move: every caller that reads the verdict off stdout keeps
    working, and the only addition is the last line."""
    _dispatch(monkeypatch, tmp_path, RESULT)
    out = capsys.readouterr().out
    [kept] = list((tmp_path / "out").glob("*.md"))
    assert out == LONG + "\n" + f"full: {kept}\n"


def test_digest_prints_the_first_lines_then_the_path_and_the_line_count(monkeypatch, tmp_path, capsys):
    rc = _dispatch(monkeypatch, tmp_path, RESULT, "--digest")
    out = capsys.readouterr().out
    assert rc == 0
    lines = out.splitlines()
    [kept] = list((tmp_path / "out").glob("*.md"))
    assert lines[: mod.DIGEST_LINES] == LONG.splitlines()[: mod.DIGEST_LINES]
    assert lines[mod.DIGEST_LINES] == f"... full: {kept} (200 lines)"
    assert len(lines) == mod.DIGEST_LINES + 1, "nothing after the path line"
    assert kept.read_text() == LONG + "\n", "the digest never shortens the file"


def test_digest_takes_a_number_and_marks_nothing_as_cut_when_nothing_was(monkeypatch, tmp_path, capsys):
    _dispatch(monkeypatch, tmp_path, RESULT, "--digest", "5")
    out = capsys.readouterr().out
    assert out.splitlines()[:5] == LONG.splitlines()[:5] and out.splitlines()[5].startswith("... full: ")
    short = {**RESULT, "result": "PASS\none clean commit"}
    _dispatch(monkeypatch, tmp_path, short, "--digest", "5")
    out = capsys.readouterr().out
    assert out.splitlines()[:2] == ["PASS", "one clean commit"]
    assert out.splitlines()[2].startswith("full: ") and out.splitlines()[2].endswith("(2 lines)")
    assert "..." not in out, "a leading `...` means lines were cut; none were"


def test_out_is_honoured_and_a_relative_out_lands_in_the_project(monkeypatch, tmp_path, capsys):
    explicit = tmp_path / "elsewhere" / "design.md"
    _dispatch(monkeypatch, tmp_path, RESULT, "--out", str(explicit))
    assert explicit.read_text() == LONG + "\n"
    assert _path_line(capsys.readouterr().out) == f"full: {explicit}"
    assert not (tmp_path / "out").exists(), "an explicit --out means no default file"
    # Relative: every wrapper `cd`s into the harness first, so the project is the anchor.
    _dispatch(monkeypatch, tmp_path, RESULT, "--out", "docs/proposed/design.md", "--digest", "1")
    assert (tmp_path / "docs" / "proposed" / "design.md").read_text() == LONG + "\n"
    assert f"... full: {tmp_path / 'docs' / 'proposed' / 'design.md'} (200 lines)" in capsys.readouterr().out


def test_a_killed_dispatch_still_writes_what_it_has_and_keeps_its_exit_code(monkeypatch, tmp_path, capsys):
    rc = _dispatch(monkeypatch, tmp_path, KILL, "--digest")
    out, err = capsys.readouterr()
    assert rc == EXIT_BUDGET
    [kept] = list((tmp_path / "out").glob("dispatch-verifier-T-1-*.md"))
    body = kept.read_text()
    assert "partial transcript, 2 step(s)" in body and "Reading the design" in body
    assert body.rstrip().endswith("Reached maximum budget ($1.5)"), "the error after the transcript, never instead of it"
    assert "partial transcript" in out and "BUDGET EXHAUSTED" in err


def test_an_unwritable_result_dir_falls_back_to_the_full_print_rather_than_losing_the_result(monkeypatch, tmp_path, capsys):
    """A digest with nowhere to point is a result lost. Never fail the dispatch either."""
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the directory should be")
    monkeypatch.setattr(mod, "RESULT_DIR", blocker / "out")
    rc = _dispatch(monkeypatch, tmp_path, RESULT, "--digest")
    out, err = capsys.readouterr()
    assert rc == 0
    assert out == LONG + "\n", "the whole result, and no path line to a file that does not exist"
    assert "result NOT kept" in err


def test_two_dispatches_of_one_agent_in_one_second_do_not_overwrite_each_other(monkeypatch, tmp_path, capsys):
    """A wave is exactly several dispatches of one agent at once."""
    monkeypatch.setattr(mod.time, "strftime", lambda *a, **k: "120000")
    _dispatch(monkeypatch, tmp_path, {**RESULT, "result": "first"})
    _dispatch(monkeypatch, tmp_path, {**RESULT, "result": "second"})
    capsys.readouterr()
    names = sorted(p.name for p in (tmp_path / "out").glob("*.md"))
    assert names == ["dispatch-verifier-T-1-120000-2.md", "dispatch-verifier-T-1-120000.md"]
    assert {p.read_text() for p in (tmp_path / "out").glob("*.md")} == {"first\n", "second\n"}


def test_an_adhoc_dispatch_is_named_as_one(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(mod, "_run_sdk", lambda *a, **k: RESULT)
    pf = tmp_path / "p.txt"
    pf.write_text("x")
    assert mod.main(["verifier", "--prompt-file", str(pf)]) == 0
    capsys.readouterr()
    [kept] = list((tmp_path / "out").glob("*.md"))
    assert kept.name.startswith("dispatch-verifier-adhoc-")


# --- tk.sh note --file: the text reaches the record without passing through the caller --


@pytest.fixture
def store(monkeypatch, tmp_path):
    s = MdTaskStore(root=tmp_path / "tasks")
    monkeypatch.setattr(tracker, "task_store", lambda *a, **k: s)
    monkeypatch.setattr("models.resolve.REPO", tmp_path)
    return s


DESIGN = "# Design\n\n```py\n  indented()\n```\n\n- a\n\n\n- b\n"


def test_a_note_from_a_file_carries_its_content_verbatim(store, tmp_path, capsys):
    tid = store.create("an epic", type="epic")
    f = tmp_path / "design.md"
    f.write_text(DESIGN)
    assert cli.main(["note", tid, "--file", str(f)]) == 0
    assert store.show(tid).notes == DESIGN.rstrip("\n"), "fences, indentation and blank lines intact"
    assert capsys.readouterr().err == ""


def test_update_append_notes_file_is_the_same_seam(store, tmp_path):
    tid = store.create("an epic", type="epic")
    f = tmp_path / "design.md"
    f.write_text(DESIGN)
    assert cli.main(["update", tid, "--status", "in_progress", "--append-notes-file", str(f)]) == 0
    t = store.show(tid)
    assert t.notes == DESIGN.rstrip("\n") and t.status == "in_progress"


@pytest.mark.parametrize(
    "argv",
    [
        ["note", "{id}", "inline text", "--file", "{f}"],
        ["note", "{id}"],
        ["update", "{id}", "--append-notes", "inline", "--append-notes-file", "{f}"],
    ],
    ids=["note-both", "note-neither", "update-both"],
)
def test_a_note_needs_exactly_one_source(store, tmp_path, capsys, argv):
    tid = store.create("an epic", type="epic")
    f = tmp_path / "design.md"
    f.write_text(DESIGN)
    argv = [a.format(id=tid, f=f) for a in argv]
    assert cli.main(argv) == 2
    assert "FAIL: " in capsys.readouterr().err
    assert store.show(tid).notes == "", "a refused note records nothing"


def test_a_missing_note_file_is_a_clean_error_not_a_traceback(store, tmp_path, capsys):
    tid = store.create("an epic", type="epic")
    assert cli.main(["note", tid, "--file", str(tmp_path / "nope.md")]) == 2
    err = capsys.readouterr().err
    assert err.startswith("FAIL: --file ") and "No such file" in err and "Traceback" not in err
    assert cli.main(["update", tid, "--append-notes-file", str(tmp_path / "nope.md")]) == 2
    assert capsys.readouterr().err.startswith("FAIL: --append-notes-file ")
    assert store.show(tid).notes == ""


def test_an_empty_note_file_is_refused_because_it_is_the_wrong_path_unnoticed(store, tmp_path, capsys):
    tid = store.create("an epic", type="epic")
    f = tmp_path / "empty.md"
    f.write_text("\n\n")
    assert cli.main(["note", tid, "--file", str(f)]) == 2
    assert "empty" in capsys.readouterr().err
    assert store.show(tid).notes == ""


def test_a_relative_note_file_resolves_against_the_project(store, tmp_path):
    """Every wrapper `cd`s into the harness before Python starts; `render --write` learned
    this the hard way and the same rule applies here."""
    tid = store.create("an epic", type="epic")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "design.md").write_text("relative design\n")
    assert cli.main(["note", tid, "--file", "docs/design.md"]) == 0
    assert store.show(tid).notes == "relative design"


def test_the_positional_text_and_an_empty_append_notes_behave_as_before(store):
    tid = store.create("an epic", type="epic")
    assert cli.main(["note", tid, "plain text"]) == 0
    assert store.show(tid).notes == "plain text"
    assert cli.main(["update", tid, "--append-notes", "", "--title", "renamed"]) == 0
    t = store.show(tid)
    assert t.notes == "plain text" and t.title == "renamed", "an empty --append-notes is still a no-op"


def test_a_dispatch_result_reaches_an_epic_note_without_passing_through_the_caller(store, monkeypatch, tmp_path, capsys):
    """The whole seam, end to end: the path `dispatch.sh --digest` prints is what
    `tk.sh note --file` takes, and the record ends up with the 200 lines the caller
    never read."""
    tid = store.create("an epic", type="epic")
    assert _dispatch(monkeypatch, tmp_path, RESULT, "--digest", "3") == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 4, "three lines and the path"
    path = _path_line(out).split("full: ", 1)[1].rsplit(" (", 1)[0]
    assert cli.main(["note", tid, "--file", path]) == 0
    assert store.show(tid).notes == LONG
