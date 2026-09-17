"""The generated epic view: readable to a human, and a failure if anyone edits it.

The value is that opening an epic's staging folder answers "what is the plan and where is
it up to" without a tracker query. The risk is that a file in the corpus which looks
authored becomes a second source of truth — so it is generated, and drift is a failure,
following the two precedents already in the tree rather than inventing a third.
"""

from __future__ import annotations

import pytest

from tracker.mdfiles import MdTaskStore
from tracker.render import BANNER, check, render_epic, write


@pytest.fixture
def epic(tmp_path):
    s = MdTaskStore(root=tmp_path / "tasks")
    e = s.create("ingest retry budget", type="epic")
    a = s.create("derive the anchor", parent=e)
    b = s.create("add the floor", parent=e)
    c = s.create("bound the input", parent=e)
    s.dep_add(c, a)
    s.close(a, "landed")
    s.update(b, status="in_progress", assignee="swarm-w2")
    return s, e, {"a": a, "b": b, "c": c}


def test_the_view_states_progress_and_every_task(epic):
    store, e, ids = epic
    text = render_epic(store, e)
    assert "ingest retry budget" in text
    assert "**closed** 1/3" in text
    for tid in ids.values():
        assert tid in text


def test_it_carries_the_generated_banner(epic):
    store, e, _ = epic
    assert render_epic(store, e).startswith(BANNER)


def test_waves_are_levelled_and_say_they_ignore_file_contention(epic):
    """An epic has been rated 11-wide whose file graph supported about two. A view that
    reports the number without the caveat invites exactly that dispatch."""
    store, e, _ = epic
    text = render_epic(store, e)
    assert "## Waves" in text and "ignores file contention" in text


def test_a_gated_task_is_marked_in_the_wave_list_not_only_below_it(epic):
    """The levelling knows about edges and nothing else, so without a marker the wave list
    and the gated section contradict each other — and the wave list is the one a reader
    acts on."""
    store, e, ids = epic
    store.gate_create(ids["c"], "owner must confirm the record number")
    text = render_epic(store, e)
    wave_block = text.split("## Waves")[1].split("## Tasks")[0]
    assert "🔒" in wave_block
    assert "0 of 1 dispatchable" in wave_block or "dispatchable" in wave_block
    assert "owner must confirm" in text, "the reason a human is needed must be stated"


def test_a_cycle_is_reported_rather_than_silently_dropped(tmp_path):
    s = MdTaskStore(root=tmp_path / "tasks")
    e = s.create("epic", type="epic")
    x = s.create("x", parent=e)
    y = s.create("y", parent=e)
    s.dep_add(x, y)
    s.dep_add(y, x)
    assert "CYCLE" in render_epic(s, e)


def test_an_unplanned_epic_says_so_rather_than_rendering_an_empty_table(tmp_path):
    s = MdTaskStore(root=tmp_path / "tasks")
    e = s.create("nothing planned yet", type="epic")
    assert "has not been planned yet" in render_epic(s, e)


# --- generated means drift is a failure ---------------------------------------


def test_writing_is_idempotent(epic, tmp_path):
    store, e, _ = epic
    dest = tmp_path / "tasks.md"
    assert write(store, e, dest) is True
    assert write(store, e, dest) is False, "an unchanged view must not churn the file"


def test_check_passes_on_a_freshly_written_view(epic, tmp_path):
    store, e, _ = epic
    dest = tmp_path / "tasks.md"
    write(store, e, dest)
    assert check(store, e, dest) is None


def test_check_FAILS_on_a_hand_edit(epic, tmp_path):
    """The guard that keeps this a view rather than a second source of truth."""
    store, e, _ = epic
    dest = tmp_path / "tasks.md"
    write(store, e, dest)
    dest.write_text(dest.read_text() + "\nsomeone edited this by hand\n")
    why = check(store, e, dest)
    assert why and "GENERATED" in why


def test_check_FAILS_when_the_tracker_moved_on(epic, tmp_path):
    """The common case: a wave landed and nobody regenerated."""
    store, e, _ = epic
    dest = tmp_path / "tasks.md"
    write(store, e, dest)
    store.create("a task added after the view was written", parent=e)
    assert check(store, e, dest) is not None


def test_check_names_the_fix_when_the_file_is_missing(epic, tmp_path):
    store, e, _ = epic
    why = check(store, e, tmp_path / "absent.md")
    assert why and "--write" in why


# --- BUG 11: the destination is in the project, and a miss is an error --------------------


def test_a_relative_destination_lands_in_the_project_not_the_harness(epic, tmp_path, monkeypatch):
    """Every wrapper cd's into the harness before Python starts, so the documented
    `--write docs/proposed/<epic>-<slug>/tasks.md` wrote into the installed plugin cache,
    exit 0, and `--check` then compared the wrong file."""
    s, e, _ = epic
    repo = tmp_path / "project"
    repo.mkdir()
    monkeypatch.setattr("models.resolve.REPO", repo)
    assert write(s, e, "docs/proposed/x/tasks.md")
    assert (repo / "docs" / "proposed" / "x" / "tasks.md").is_file()
    assert check(s, e, "docs/proposed/x/tasks.md") is None, "--check reads the same file --write wrote"


def test_a_relative_destination_that_escapes_the_project_is_refused(epic, tmp_path, monkeypatch):
    from tracker.port import TrackerError

    s, e, _ = epic
    repo = tmp_path / "project"
    repo.mkdir()
    monkeypatch.setattr("models.resolve.REPO", repo)
    with pytest.raises(TrackerError, match="outside the project"):
        write(s, e, "../elsewhere/tasks.md")
    assert not (tmp_path / "elsewhere").exists()


def test_the_bare_id_renders_the_same_epic_as_the_prefixed_one(epic):
    s, e, ids = epic
    bare = e.split("-", 1)[1]
    assert render_epic(s, bare) == render_epic(s, e)
    assert "has not been planned yet" not in render_epic(s, bare)


def test_an_unknown_epic_is_an_error_not_an_unplanned_view(epic):
    from tracker.port import TrackerError

    s, _, _ = epic
    with pytest.raises(TrackerError, match="no such epic"):
        render_epic(s, "t-nope")
