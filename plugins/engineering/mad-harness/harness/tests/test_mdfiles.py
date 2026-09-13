"""The markdown backend's own design decisions — the ones the shared contract cannot see.

The conformance contract asserts what every backend must do. These assert what THIS one
does differently and why: a two-tier store, an archive that keeps closed references
resolvable, decisions that never compact, and a tracked export separate from the
worker-writable hot store.
"""

from __future__ import annotations

import json

import pytest

from tracker.mdfiles import MdMemoryStore, MdTaskStore
from tracker.port import CLOSED, DECISION, EPIC, TASK


@pytest.fixture
def store(tmp_path):
    return MdTaskStore(root=tmp_path / "tasks", export_dir=tmp_path / "export")


# --- the two tiers ------------------------------------------------------------


def test_a_closed_record_leaves_the_hot_store(store):
    """`ready` parses the directory on every call, so unbounded retention makes the
    scheduler slower every wave. That cost does not exist in a database, which is why
    this tier is backend-specific rather than in the port."""
    tid = store.create("will close")
    assert store._path(tid).exists()
    store.close(tid, "done")
    assert not store._path(tid).exists(), "a closed record must not stay in the hot store"


def test_a_closed_record_is_still_resolvable_from_the_archive(store):
    tid = store.create("will close")
    store.close(tid, "the reason")
    got = store.show(tid)
    assert got is not None and got.status == CLOSED
    assert got.id == tid


def test_a_closed_dependency_reads_as_SATISFIED_not_missing(store):
    """The distinction the scheduler cannot guess. If a closed blocker vanished, its
    dependent would look either permanently blocked or wrongly ready, depending on how
    the absence was interpreted — and both are wrong for the same reason."""
    blocker = store.create("blocker")
    dependent = store.create("dependent")
    store.dep_add(dependent, blocker)
    store.close(blocker, "done")
    assert dependent in {t.id for t in store.ready()}
    assert store.show(blocker) is not None, "the edge target must still resolve"


def test_a_CLOSED_DECISION_never_leaves_the_hot_store(store):
    """A closed decision is settled, NOT irrelevant: the survey reads them as binding
    constraints and the register check verifies their status. Compacting one would answer
    those questions from a summary that has dropped its resolution."""
    did = store.create("an owner decision", type=DECISION)
    store.close(did, "chose option B")
    assert store._path(did).exists(), "decisions stay hot whatever their status"
    assert store.show(did).description or store.show(did).raw.get("close_reason")


def test_the_archive_survives_a_torn_final_line(store, tmp_path):
    """A crash mid-append must not cost the whole history."""
    a = store.create("one")
    store.close(a, "done")
    with store._archive.open("a") as fh:
        fh.write('{"id": "half-writ')
    assert store.show(a) is not None, "a torn last line must not lose earlier records"


# --- gates --------------------------------------------------------------------


def test_an_open_gate_withholds_its_target_from_ready(store):
    target = store.create("gated work")
    store.gate_create(target, "owner decision owed")
    assert target not in {t.id for t in store.ready()}


def test_resolving_the_gate_releases_the_target(store):
    target = store.create("gated work")
    gid = store.gate_create(target, "owner decision owed")
    store.gate_resolve(gid)
    assert target in {t.id for t in store.ready()}


def test_a_gate_is_never_offered_as_work(store):
    store.gate_create(store.create("x"), "reason")
    assert all(t.type != "gate" for t in store.ready())


# --- the tracked export -------------------------------------------------------


def test_export_writes_per_record_markdown_not_a_blob(tmp_path, store):
    """The single thing this backend buys over tasks: a reviewable diff. `issues.jsonl`
    is one blob whose changes read as noise."""
    a = store.create("first", description="body text")
    store.create("second")
    store.export()
    written = sorted(p.name for p in (tmp_path / "export").glob("*.md"))
    assert len(written) == 2 and f"{a}.md" in written
    assert "body text" in (tmp_path / "export" / f"{a}.md").read_text()


def test_export_carries_the_archive_so_history_is_versioned_too(tmp_path, store):
    a = store.create("closes")
    store.close(a, "done")
    store.export()
    archived = (tmp_path / "export" / "archive.jsonl").read_text()
    assert a in archived and json.loads(archived.splitlines()[0])["id"] == a


def test_export_drops_records_that_no_longer_exist(tmp_path, store):
    """A stale file in the tracked copy is a record that appears to still be open."""
    a = store.create("will be deleted")
    store.export()
    store.delete(a)
    store.export()
    assert not (tmp_path / "export" / f"{a}.md").exists()


def test_export_refuses_without_a_destination(tmp_path):
    from tracker.port import TrackerError

    bare = MdTaskStore(root=tmp_path / "tasks")
    with pytest.raises(TrackerError, match="no export directory"):
        bare.export()


# --- the hot store is addressed by env, exactly as BEADS_DB is ----------------


def test_the_hot_store_follows_TASKS_DIR(tmp_path, monkeypatch):
    """Workers reach the PRIMARY checkout's store by absolute path, whatever branch their
    worktree has checked out. Location is not what makes it shared — this is."""
    from tracker.mdfiles import TASKS_DIR_ENV

    monkeypatch.setenv(TASKS_DIR_ENV, str(tmp_path / "elsewhere"))
    assert MdTaskStore().root == tmp_path / "elsewhere"


def test_ids_are_random_so_concurrent_creates_cannot_collide(store):
    """A counter would need a lock around every create, and a lost update there hands two
    records one id — which the archive then cannot tell apart."""
    ids = {store.create(f"t{i}") for i in range(50)}
    assert len(ids) == 50


def test_a_partial_write_is_never_visible(store):
    """Whole-file write via os.replace: a reader sees the old file or the new one."""
    tid = store.create("original", description="first")
    store.update(tid, description="second")
    assert store.show(tid).description == "second"
    assert not list(store.root.glob(".w-*")), "no temporary files left behind"


# --- capabilities are honest --------------------------------------------------


def test_this_backend_declares_no_record_ceiling(store):
    """Every guard that exists because tasks fails closed past ~64KB is inert here, and
    says so rather than warning about nothing forever."""
    assert store.capabilities().record_bytes is None


def test_owned_paths_name_the_configured_store_and_export(tmp_path, monkeypatch):
    """The VALUES, which the shared contract cannot assert because they differ by backend.

    A declaration that drifts from where the backend actually writes is worse than none:
    it reads as a passing guard while the sweep it protects walks into the tracker's own
    files.
    """
    repo = tmp_path / "repo"
    (repo / ".harness" / "tasks").mkdir(parents=True)
    (repo / "docs" / "tasks").mkdir(parents=True)
    monkeypatch.setenv("MAD_HARNESS_REPO", str(repo))

    store = MdTaskStore(root=repo / ".harness" / "tasks", export_dir=repo / "docs" / "tasks")
    owned = store.capabilities().owned_paths

    assert ".harness/tasks/" in owned, f"the hot store is missing from {owned}"
    assert "docs/tasks/" in owned, f"the tracked export is missing from {owned}"

    # Where the backend really writes, checked rather than assumed.
    tid = store.create("lands under a declared path")
    store.export(str(repo / "docs" / "tasks"))
    for written in (p for p in repo.rglob("*") if p.is_file()):
        rel = written.relative_to(repo).as_posix()
        assert any(rel.startswith(o) for o in owned), f"{rel} is under no declared path"
    assert store.show(tid) is not None


def test_owned_paths_follow_the_configured_location(tmp_path, monkeypatch):
    """Not hardcoded: a project that puts its export elsewhere gets that answer back."""
    repo = tmp_path / "repo"
    (repo / "state" / "tk").mkdir(parents=True)
    (repo / "planning").mkdir(parents=True)
    monkeypatch.setenv("MAD_HARNESS_REPO", str(repo))

    store = MdTaskStore(root=repo / "state" / "tk", export_dir=repo / "planning")
    assert set(store.capabilities().owned_paths) == {"state/tk/", "planning/"}


def test_notes_append_and_survive_a_round_trip(store):
    tid = store.create("accumulates")
    store.note(tid, "first")
    store.note(tid, "second")
    notes = store.show(tid).notes
    assert "first" in notes and "second" in notes


def test_an_epic_validates_into_waves(store):
    epic = store.create("epic", type=EPIC)
    a = store.create("a", type=TASK, parent=epic)
    b = store.create("b", type=TASK, parent=epic)
    store.dep_add(b, a)
    v = store.validate(epic)
    assert [w.task_ids for w in v.waves] == [(a,), (b,)]


# --- memories -----------------------------------------------------------------


def test_memories_round_trip(tmp_path):
    mem = MdMemoryStore(tmp_path / "tasks")
    mem.remember("uv sync hardlinks from a global cache", key="uv-is-cheap")
    assert "uv-is-cheap" in mem.memories()
    assert any("hardlinks" in m for m in mem.recall("hardlink"))


def test_recall_returns_nothing_rather_than_everything_on_a_miss(tmp_path):
    mem = MdMemoryStore(tmp_path / "tasks")
    mem.remember("something recorded")
    assert mem.recall("a phrase that appears nowhere") == []
