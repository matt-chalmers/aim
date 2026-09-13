"""The contract every TaskStore must satisfy, run against every shipped backend.

WHY A SHARED CONTRACT AND NOT PER-BACKEND TESTS. "Full parity" is a claim, and a claim
about behaviour is only worth what a test says. Per-backend suites drift: each grows the
assertions its own implementation happens to make easy, and the day a caller swaps
backends it meets a difference nobody wrote down. One contract, parameterised, makes a
difference impossible to introduce quietly.

WRITTEN BEFORE THE SECOND BACKEND EXISTS, deliberately. A conformance suite written
afterwards documents whatever the second implementation already does; written first, it is
the specification the second implementation has to meet.

AGAINST A LIVE TRACKER, NOT A FAKE. The tasks case builds a real scratch workspace and
drives the real binary. A mocked `bd` would only prove the adapter agrees with my
assumptions about it — and two of those assumptions were wrong when first checked against
the real CLI: `create` printed its id inside human prose, and `list` omits `dependencies`
entirely until an edge exists.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tracker.beads import BeadsTaskStore
from tracker.port import CLOSED, DECISION, EPIC, TASK, Task

pytestmark = pytest.mark.conformance

HAVE_BD = shutil.which("bd") is not None


def _beads_workspace(tmp_path):
    """A real, throwaway beads workspace in its own git repo.

    ONE PER SESSION, not one per test. `git init` + `bd init` costs about five seconds,
    and sixteen of them took the harness suite from roughly a second to 87 — breaking the
    property `pytest.ini` states as the point of this suite, that an application test run
    is never gated on slow harness hygiene.

    Sharing is safe because every assertion below is about MEMBERSHIP of a record this
    test created, never about the store being otherwise empty. That is a constraint on
    tests added here: assert `mine in listed`, never `listed == [mine]`.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "init", "-q", "."], cwd=ws, check=True)
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "init"],
        cwd=ws,
        check=True,
        capture_output=True,
    )
    r = subprocess.run(["bd", "init"], cwd=ws, capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"bd init failed: {r.stderr[:200]}")
    return BeadsTaskStore(cwd=str(ws))


@pytest.fixture(
    scope="session",
    params=[
        pytest.param(
            "beads",
            marks=pytest.mark.skipif(not HAVE_BD, reason="bd is not installed"),
        ),
        "mdfiles",
    ],
)
def store(request, tmp_path_factory):
    if request.param == "beads":
        return _beads_workspace(tmp_path_factory.mktemp("beads"))
    if request.param == "mdfiles":
        from tracker.mdfiles import MdTaskStore

        return MdTaskStore(root=tmp_path_factory.mktemp("mdfiles") / "tasks")
    raise AssertionError(f"no fixture for backend {request.param!r}")


# --- the record round-trip ----------------------------------------------------


def test_create_returns_an_id_that_show_resolves(store):
    """A write that reports the wrong id has corrupted the record it just made."""
    tid = store.create("a task", type=TASK, description="body")
    assert tid, "create must return an id"
    got = store.show(tid)
    assert got is not None and got.id == tid
    assert got.title == "a task" and got.description == "body"
    assert got.type == TASK and got.is_open


def test_show_returns_none_for_an_unknown_id(store):
    assert store.show("definitely-not-a-real-id") is None


def test_show_still_resolves_a_CLOSED_record(store):
    """The rule the retention design turns on: a reference to a closed task must resolve.

    If it returned None, "dependency satisfied" would be indistinguishable from
    "dependency missing" — and the scheduler would have to guess which.
    """
    tid = store.create("will be closed")
    store.close(tid, "done for the day")
    got = store.show(tid)
    assert got is not None and got.status == CLOSED


def test_close_RECORDS_the_reason_and_not_merely_the_status(store):
    """`close` takes a required reason because a close that records nothing is exactly
    what the argument exists to prevent. Asserting only the status let a backend accept
    the reason and drop it — which one did, silently, until a backend-specific test
    happened to look.
    """
    tid = store.create("closes with a reason")
    store.close(tid, "a distinctive reason phrase")
    got = store.show(tid)
    assert got.status == CLOSED
    # THE PORT ANSWERS THIS NOW. Searching description, notes, title AND raw was the
    # contract admitting the port had no field for it, which left every caller either
    # branching on `raw` — explicitly forbidden — or guessing. A worker resuming after an
    # operator refused its request needs to be told why.
    assert got.close_reason == "a distinctive reason phrase"
    haystack = " ".join(
        [got.description, got.notes, got.title, json.dumps(got.raw, default=str)]
    )
    assert "a distinctive reason phrase" in haystack, (
        "the close reason must survive somewhere a reader can find it"
    )


def test_note_appends_and_never_replaces(store):
    """Notes are the audit trail the lenses build; an overwrite loses provenance."""
    tid = store.create("accumulates notes")
    store.note(tid, "first observation")
    store.note(tid, "second observation")
    notes = store.show(tid).notes
    assert "first observation" in notes and "second observation" in notes


# --- the dependency graph -----------------------------------------------------


def test_a_dependency_edge_is_readable_from_list(store):
    """`list` is what every check reads. An edge invisible there makes the blocking-prose
    check report a missing edge for every task that has one."""
    blocker = store.create("the blocker")
    dependent = store.create("the dependent")
    store.dep_add(dependent, blocker)

    from_list = {t.id: t for t in store.list()}
    assert blocker in from_list[dependent].depends_on, (
        "list() must expose edges; the check that reads them is the one that finds "
        "records blocked in prose only"
    )
    assert blocker in store.show(dependent).depends_on, "show() must agree with list()"


def test_ready_excludes_a_task_whose_blocker_is_open(store):
    blocker = store.create("blocker stays open")
    dependent = store.create("must not be offered")
    store.dep_add(dependent, blocker)
    offered = {t.id for t in store.ready()}
    assert blocker in offered
    assert dependent not in offered, "a task with an open blocker is not dispatchable"


def test_ready_offers_a_task_once_its_blocker_closes(store):
    blocker = store.create("blocker will close")
    dependent = store.create("becomes dispatchable")
    store.dep_add(dependent, blocker)
    store.close(blocker, "unblocking")
    assert dependent in {t.id for t in store.ready()}


def test_validate_levels_an_epic_into_waves(store):
    epic = store.create("an epic", type=EPIC)
    a = store.create("first", parent=epic)
    b = store.create("second", parent=epic)
    store.dep_add(b, a)
    v = store.validate(epic)
    if not v.waves:
        pytest.skip("backend does not model parent/child for this shape")
    flat = [tid for w in v.waves for tid in w.task_ids]
    assert flat.index(a) < flat.index(b), "a blocker must be levelled before its dependent"


# --- filters ------------------------------------------------------------------


def test_list_filters_by_type(store):
    store.create("a plain task", type=TASK)
    d = store.create("a decision", type=DECISION)
    ids = {t.id for t in store.list(type=DECISION)}
    assert d in ids
    assert all(t.type == DECISION for t in store.list(type=DECISION))


def test_list_by_status_open_excludes_closed(store):
    keep = store.create("stays open")
    gone = store.create("gets closed")
    store.close(gone, "closed")
    ids = {t.id for t in store.list(status="open")}
    assert keep in ids and gone not in ids


# --- gates --------------------------------------------------------------------


def test_a_gate_is_created_and_listed(store):
    target = store.create("the gated epic", type=EPIC)
    gid = store.gate_create(target, "an owner decision is owed")
    assert gid, "gate_create must return the gate's id"
    assert gid in {g.id for g in store.gate_list()}


def test_resolving_a_gate_removes_it_from_the_open_list(store):
    target = store.create("gated then released", type=EPIC)
    gid = store.gate_create(target, "temporary")
    store.gate_resolve(gid)
    assert gid not in {g.id for g in store.gate_list()}


# --- supersede ----------------------------------------------------------------


def test_supersede_closes_the_old_record_and_keeps_it_readable(store):
    """`supersede` is preferred over `delete` where there is history worth keeping, so
    the superseded record must survive as something a later reader can resolve."""
    old = store.create("the original")
    new = store.create("the replacement")
    store.supersede(old, new)
    got = store.show(old)
    assert got is not None and got.status == CLOSED


# --- capabilities are declared, never guessed ---------------------------------


def test_the_backend_declares_its_capabilities(store):
    caps = store.capabilities()
    assert caps.name
    assert caps.record_bytes is None or caps.record_bytes > 0


def test_every_backend_declares_the_paths_it_owns(store):
    """Consumers must be able to ASK which paths are the tracker's, not assume.

    A doc-drift sweep over `docs/**`, a differential comparing two backends, a
    .gitignore generator: each has to skip the tracker's own files, and each would
    otherwise hardcode `.beads/` and go quietly wrong under every other backend.

    THE CONTRACT PINS THE SHAPE, NOT THE VALUES. What the paths are is backend-specific
    by definition — that is the whole reason for asking rather than assuming — so the
    values are asserted in each backend's own tests, where they are known.
    """
    caps = store.capabilities()
    assert caps.owned_paths, f"{caps.name} declares no owned paths"
    for path in caps.owned_paths:
        assert path.endswith("/"), (
            f"{path!r} must end in / — consumers match it as a directory prefix, and "
            f"a bare 'docs/task' would also swallow 'docs/tasks-for-humans.md'"
        )
        assert path.strip("/"), f"{path!r} would match the entire repository"


def test_a_task_from_any_backend_carries_the_fields_callers_branch_on(store):
    tid = store.create("field coverage", type=TASK, description="d")
    t = store.show(tid)
    for field in ("id", "type", "status", "title"):
        assert getattr(t, field) != "", f"{field} must be populated by every backend"
    assert isinstance(t, Task)


# --- verbs the prompts invoke, which the contract must therefore cover ---------


def test_update_sets_a_field(store):
    tid = store.create("will be blocked")
    store.update(tid, status="blocked")
    assert store.show(tid).status == "blocked"


def test_label_adds_and_removes(store):
    tid = store.create("labelled")
    store.label(tid, "awaiting-approval")
    assert "awaiting-approval" in store.show(tid).labels
    store.label(tid, "awaiting-approval", remove=True)
    assert "awaiting-approval" not in store.show(tid).labels


def test_autosync_is_accepted_by_every_backend(store):
    """A wave disables it at pre-flight and restores it at close. A backend with nothing
    to disable must SUCCEED rather than refuse — refusing would make a caller believe the
    wave is unsafe in exactly the case that is safe."""
    store.autosync(False)
    store.autosync(True)


def test_an_unsupported_capability_refuses_rather_than_answering_emptily(store):
    """`prime` exists only on tasks. The one that lacks it must raise, never return "" —
    an empty answer reads as "primed, and there was nothing to say"."""
    from tracker.port import NotSupported

    if store.capabilities().prime:
        assert isinstance(store.prime(), str)
    else:
        with pytest.raises(NotSupported):
            store.prime()


def test_delete_removes_a_record(store):
    tid = store.create("to be deleted")
    store.delete(tid)
    assert store.show(tid) is None


# --- the retention contract, which both backends owe -------------------------


def test_a_closed_blocker_reads_as_SATISFIED_not_missing(store):
    """The distinction the scheduler cannot guess.

    If a closed dependency stopped resolving, its dependent would look either permanently
    blocked or wrongly ready depending on how the absence was read — and both are wrong
    for the same reason. Any backend that compacts closed records must keep them
    resolvable.
    """
    blocker = store.create("blocker that will close")
    dependent = store.create("dependent")
    store.dep_add(dependent, blocker)
    store.close(blocker, "done")

    assert store.show(blocker) is not None, "a closed blocker must still resolve"
    assert dependent in {t.id for t in store.ready()}, (
        "its dependent must become dispatchable, not stay blocked on a record that is gone"
    )


def test_a_CLOSED_DECISION_is_still_listed_as_a_decision(store):
    """A closed decision is settled, not irrelevant. The survey reads them as binding
    constraints and the register check verifies their status, both long after close."""
    d = store.create("an owner decision", type=DECISION)
    store.close(d, "chose option B")
    assert d in {t.id for t in store.list(type=DECISION)}


def test_ready_never_offers_a_closed_task(store):
    """The other half: resolvable must not mean schedulable."""
    tid = store.create("closes")
    store.close(tid, "done")
    assert tid not in {t.id for t in store.ready()}


def test_a_superseded_record_points_at_its_replacement(store):
    """`supersede` is preferred over `delete` where there is history worth keeping, so the
    link has to survive or the preference buys nothing."""
    old = store.create("the original")
    new = store.create("the replacement")
    store.supersede(old, new)
    got = store.show(old)
    haystack = " ".join([got.description, got.notes, json.dumps(got.raw, default=str)])
    assert new in haystack, "the replacement's id must be recoverable from the old record"


def test_every_record_type_the_harness_uses_is_accepted(store):
    """A type one backend accepts and the other rejects is a silent one-backend feature.

    Found the hard way: a `permission` record type worked on mdfiles and failed on tasks
    with "validation failed: invalid issue type: permission", because that backend
    validates its type vocabulary. The contract asserted plenty about behaviour and
    nothing about which types were usable, so nothing caught it until a live filing.
    """
    from tracker.port import DECISION, EPIC, TASK

    for kind in (TASK, EPIC, DECISION):
        tid = store.create(f"type probe {kind}", type=kind)
        got = store.show(tid)
        assert got is not None, f"{kind} was created but does not resolve"
        assert got.type == kind, f"{kind} came back as {got.type}"


# --- cross-backend migration --------------------------------------------------


def test_a_migration_preserves_the_graph_not_just_the_records(store, tmp_path):
    """A backend switch mid-flight used to strand records in the old store.

    Ids cannot be preserved — each backend mints its own — so every edge has to be
    rewritten through a map built while creating. Copying the rows and losing the edges
    would look like a successful migration and produce a backlog where nothing blocks
    anything.

    Parameterised over backends, so this runs FROM each shipped backend.
    """
    from tracker.mdfiles import MdTaskStore
    from tracker.migrate import migrate

    epic = store.create("migration probe epic", type=EPIC)
    a = store.create("probe leaf a", type=TASK, parent=epic)
    b = store.create("probe leaf b", type=TASK, parent=epic)
    c = store.create("probe dependent", type=TASK, parent=epic)
    store.dep_add(c, a)
    store.dep_add(c, b)
    store.close(a, "a distinctive migration reason")

    target = MdTaskStore(root=tmp_path / "migrated")
    result = migrate(store, target)
    assert result.ok, result.skipped

    moved = {t.title: t for t in target.list()}
    for title in ("migration probe epic", "probe leaf a", "probe leaf b", "probe dependent"):
        assert title in moved, f"{title} did not migrate"

    # the edges, rewritten through the id map
    dependent = moved["probe dependent"]
    blockers = sorted(target.show(d).title for d in dependent.depends_on)
    assert blockers == ["probe leaf a", "probe leaf b"]

    # the parent edge
    assert target.show(dependent.parent).title == "migration probe epic"

    # status AND reason — a closed record arriving open would silently re-open work
    assert moved["probe leaf a"].status == CLOSED
    assert "a distinctive migration reason" in moved["probe leaf a"].close_reason

    # ids are remapped, not reused
    assert not set(result.created) & set(result.created.values())


def test_a_migration_reports_what_it_could_not_move(store, tmp_path):
    """Silence on failure is the dangerous outcome: a partial migration that looks whole.

    The source is never modified, so a reported failure is recoverable by fixing the
    record and re-running into a clean target.
    """
    from tracker.migrate import Migration

    empty = Migration(created={}, edges=0, closed=0, skipped=("x: refused",))
    assert not empty.ok
    assert "SKIPPED" in empty.summary()

    before = len(store.list())
    from tracker.mdfiles import MdTaskStore
    from tracker.migrate import migrate

    migrate(store, MdTaskStore(root=tmp_path / "readonly-check"))
    assert len(store.list()) == before, "migration must not modify the source"
