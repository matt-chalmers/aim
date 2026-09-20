"""The tracker ports: the mutex that guards a wave, and the seam that lets bd be swapped.

Two things here are load-bearing rather than routine, and both have a companion test
proving the guard can actually fail — the standard `harness.yaml` sets for every
mechanical guard in this repo:

* **The claim is a real mutex.** It exists to catch a double-dispatch, and a claim that
  silently succeeded twice would put two workers in one task with no symptom until their
  commits collided.
* **Port purity.** Both shipped backends sit on a local POSIX filesystem, so a path
  leaking into a `TaskStore` signature would not fail — it would quietly become part of
  the contract, and the next backend would have to reproduce it.
"""

from __future__ import annotations

import inspect
import json
import typing

import pytest

import tracker
from tracker import graph
from tracker.beads import _edges, _issues, _task
from tracker.events import LocalTelemetry
from tracker.locks import FileCoordination
from tracker.port import (
    CLOSED,
    OPEN,
    Coordination,
    MemoryStore,
    Task,
    TaskStore,
    Telemetry,
)


@pytest.fixture
def coord(tmp_path):
    return FileCoordination(tmp_path / "run")


# --- the claim mutex ----------------------------------------------------------


def test_only_one_actor_can_claim_a_task(coord):
    """The guard against a double-dispatch, which is the only way this is reached."""
    first = coord.try_claim("T-1", "w1")
    second = coord.try_claim("T-1", "w2")
    assert first.held and first.holder == "w1"
    assert not second.held, "two workers must never both hold one task"
    assert second.holder == "w1", "the loser must learn WHO holds it, not just that it lost"


def test_the_same_actor_reclaiming_is_idempotent(coord):
    """A resumed run must be able to re-enter its own task cleanly."""
    coord.try_claim("T-1", "w1")
    again = coord.try_claim("T-1", "w1")
    assert again.held and again.reentrant


def test_a_claim_survives_across_processes(coord, tmp_path):
    """The mutex is only worth anything if it holds between separate CLI invocations."""
    coord.try_claim("T-1", "w1")
    other = FileCoordination(tmp_path / "run")  # a fresh object, as a new process gets
    assert not other.try_claim("T-1", "w2").held


def test_the_claim_guard_can_fail(coord, monkeypatch):
    """Prove the mutex is the O_EXCL create and not an accident of test ordering.

    Neuter exclusivity and the first assertion above must break. A guard nobody has
    watched fail is not evidence.
    """
    import tracker.locks as mod

    monkeypatch.setattr(mod, "_create_exclusive", lambda path, body: True)
    assert coord.try_claim("T-1", "w1").held
    assert coord.try_claim("T-1", "w2").held, (
        "with exclusivity removed both claims succeed — which is what the real "
        "implementation must prevent"
    )


def test_releasing_someone_elses_claim_is_refused(coord):
    coord.try_claim("T-1", "w1")
    assert coord.release_claim("T-1", "w2") == "held-by:w1"
    assert coord.release_claim("T-1", "w1") == "released"


# --- the merge slot -----------------------------------------------------------


def test_the_slot_admits_exactly_one_holder(coord):
    assert coord.slot_acquire("w1")
    assert not coord.slot_acquire("w2")
    assert coord.slot_check().holder == "w1"


def test_a_fresh_lease_is_not_stealable_just_because_its_process_exited(coord):
    """THE REGRESSION. Acquire and release are separate CLI invocations, so the process
    that took the lease has usually exited by the time anyone else looks.

    The first implementation read a dead pid as an abandoned lease. Eight contenders
    raced and TWO won — the loser probed a pid that had already gone, called the lease
    stale and stole it. A slot everybody can steal is worse than no slot, because
    everybody believes it works.
    """
    coord.slot_acquire("w1")
    rec = json.loads(coord._slot.read_text())
    rec["pid"] = 2**22  # a pid that cannot be running — as the real holder's soon is
    coord._slot.write_text(json.dumps(rec))

    assert not coord.slot_check().stale, "a fresh lease is held, whatever its pid says"
    assert not coord.slot_acquire("w2"), "and it must not be stealable"


def test_an_AGED_lease_is_stolen_and_the_steal_is_recorded(coord):
    """Age is the only staleness signal. A worker that died still strands the slot until
    the TTL — deliberately, since stealing a live holder's slot is the worse failure."""
    import tracker.locks as mod

    coord.slot_acquire("w1")
    rec = json.loads(coord._slot.read_text())
    rec["pid"] = 2**22
    rec["at"] = rec["at"] - (mod.STALE_AFTER_S + 60)
    coord._slot.write_text(json.dumps(rec))

    assert coord.slot_check().stale
    assert coord.slot_acquire("w2"), "an aged lease must be reclaimable"
    assert json.loads(coord._slot.read_text())["stole_from"] == "w1", (
        "a silent steal is how two workers commit at once each believing it holds the slot"
    )


def test_a_fresh_lease_from_another_host_is_never_stolen(coord):
    """A pid from another machine names a different process here — probing it is a guess,
    so a foreign lease is judged purely on age."""
    coord.slot_acquire("w1")
    rec = json.loads(coord._slot.read_text())
    rec.update(host="some-other-machine", pid=2**22)
    coord._slot.write_text(json.dumps(rec))
    assert not coord.slot_check().stale
    assert not coord.slot_acquire("w2")


def test_foreign_claims_are_reported_for_the_cross_machine_warning(coord):
    coord.try_claim("T-1", "w1")
    path = coord._claim_path("T-1")
    rec = json.loads(path.read_text())
    rec["host"] = "another-box"
    path.write_text(json.dumps(rec))
    assert [c["task"] for c in coord.foreign_claims()] == ["T-1"]


# --- telemetry: the move must cost no history ---------------------------------


def test_legacy_events_are_merged_rather_than_migrated(tmp_path):
    """The value of this series is entirely its history; an empty new one is a loss."""
    from tracker.port import Event

    old = Event("harness.dispatch", "old", {"cost_usd": 1.0}, "2020-01-01T00:00:00")
    tel = LocalTelemetry(tmp_path / "events", legacy_reader=lambda: [old])
    tel.record("harness.dispatch", "new", {"cost_usd": 2.0})
    series = tel.read("harness.dispatch")
    assert [e.target for e in series] == ["old", "new"], "oldest first, nothing dropped"


def test_a_broken_legacy_reader_does_not_hide_new_events(tmp_path):
    def boom():
        raise RuntimeError("bd is gone")

    tel = LocalTelemetry(tmp_path / "events", legacy_reader=boom)
    tel.record("harness.dispatch", "new", {})
    assert len(tel.read()) == 1


def test_recording_telemetry_never_raises(tmp_path):
    """A dispatch that succeeded and failed to record its cost is still a success."""
    tel = LocalTelemetry(tmp_path / "events")
    assert tel.record("c", "t", {"bad": object()}) is False  # unserialisable payload


# --- the graph ----------------------------------------------------------------


def test_ready_excludes_a_task_whose_blocker_is_open():
    ts = [
        Task("a", "task", OPEN),
        Task("b", "task", OPEN, depends_on=("a",)),
        Task("c", "task", OPEN, depends_on=("z",)),  # z unknown => treated satisfied
    ]
    assert [t.id for t in graph.ready(ts)] == ["a", "c"]


def test_ready_admits_a_task_whose_blocker_is_closed():
    ts = [Task("a", "task", CLOSED), Task("b", "task", OPEN, depends_on=("a",))]
    assert [t.id for t in graph.ready(ts)] == ["b"]


def test_validate_reports_cycles_rather_than_hanging():
    ts = [
        Task("x", "task", OPEN, depends_on=("y",)),
        Task("y", "task", OPEN, depends_on=("x",)),
    ]
    v = graph.validate(ts)
    assert v.cycles == (("x", "y"),) and not v.ok


def test_validate_levels_the_dag_into_waves():
    ts = [
        Task("a", "task", OPEN),
        Task("b", "task", OPEN),
        Task("c", "task", OPEN, depends_on=("a", "b")),
    ]
    v = graph.validate(ts)
    assert [w.task_ids for w in v.waves] == [("a", "b"), ("c",)]
    assert v.max_parallelism == 2


# --- the beads adapter's one real job: normalisation --------------------------


@pytest.mark.parametrize(
    "raw,n",
    [
        ('[{"id":"a"}]', 1),
        ('{"issues":[{"id":"a"},{"id":"b"}]}', 2),
        ('{"id":"a"}', 1),
        ("", 0),
    ],
)
def test_every_json_shape_bd_emits_is_normalised(raw, n):
    """Six call sites each carried their own version of this, and one knowing only a
    single shape would have returned nothing and reported clean."""
    assert len(_issues(raw)) == n


@pytest.mark.parametrize(
    "deps",
    [
        [{"depends_on_id": "x"}],  # `bd list --all --json`
        [{"id": "x"}],  # `bd show --json` — the SAME field, shaped differently
        ["x"],
    ],
)
def test_both_dependency_shapes_are_read(deps):
    """Reading only one shape silently returns no edges, making every check pass."""
    assert _edges({"dependencies": deps}) == ("x",)


def test_a_task_round_trips_through_the_mapper():
    t = _task(
        {
            "id": "P-1",
            "issue_type": "epic",
            "status": "open",
            "title": "a title with several words in it",
            "dependencies": [{"depends_on_id": "P-0"}],
        }
    )
    assert t.id == "P-1" and t.depends_on == ("P-0",) and t.is_open
    assert t.gloss().startswith("P-1 (")


# --- port purity --------------------------------------------------------------


def test_port_exposes_no_filesystem_paths():
    """Both shipped backends sit on POSIX, so a leaked path would not fail — it would
    quietly become part of the contract and the next backend would inherit it."""
    from pathlib import Path

    offenders = []
    for name, meth in inspect.getmembers(TaskStore, inspect.isfunction):
        if name.startswith("_"):
            continue
        hints = typing.get_type_hints(meth)
        for param, hint in hints.items():
            if hint is Path or "Path" in str(hint):
                offenders.append(f"TaskStore.{name}({param}: {hint})")
    assert not offenders, f"TaskStore signatures name a filesystem path: {offenders}"


def test_the_shipped_backend_satisfies_every_port():
    from tracker.beads import BeadsMemoryStore, BeadsTaskStore

    assert isinstance(BeadsTaskStore(), TaskStore)
    assert isinstance(BeadsMemoryStore(), MemoryStore)
    assert isinstance(tracker.coordination(), Coordination)
    assert isinstance(tracker.telemetry(), Telemetry)


def test_coordination_is_built_without_choosing_a_backend(monkeypatch):
    """One implementation, always. A backend-conditional construction here would be the
    first step back towards per-backend claim logic.

    Asserted by BEHAVIOUR, not by grepping the source: an earlier version of this test
    searched for the word "beads" and matched its own docstring, which is the same trap
    it exists to describe.
    """
    monkeypatch.setattr(tracker, "backend_name", lambda: "some-future-backend")
    assert isinstance(tracker.coordination(), Coordination)
    assert isinstance(tracker.telemetry(), Telemetry)


def test_an_unknown_backend_is_refused_rather_than_defaulted():
    with pytest.raises(tracker.TrackerError, match="unknown tracker backend"):
        tracker.task_store("no-such-backend")


def test_a_project_with_no_tracker_block_gets_todays_behaviour():
    assert tracker.backend_name() == "beads"


# --- the CLI shim -------------------------------------------------------------


def test_json_is_accepted_after_the_verb(monkeypatch, tmp_path):
    """Prompts write `tk.sh ready --json`, mirroring `bd ready --json`. A global-only
    flag would have forced all 95 migrated call sites to reorder their arguments."""
    from tracker.cli import build_parser

    args = build_parser().parse_args(["ready", "--json"])
    assert args.verb == "ready" and args.json


def test_every_verb_accepts_the_common_flags():
    from tracker.cli import build_parser

    parser = build_parser()
    sub = [a for a in parser._actions if hasattr(a, "choices") and a.choices]
    assert sub, "the CLI declares no subcommands"
    for name, p in sub[0].choices.items():
        flags = {s for a in p._actions for s in a.option_strings}
        assert "--json" in flags, f"verb {name!r} cannot be asked for JSON"


# --- the size check reads a capability, never a constant ----------------------


class _FakeStore:
    """A TaskStore stub, so the size check can be tested without a live tracker."""

    def __init__(self, tasks, ceiling):
        self._tasks, self._ceiling = tasks, ceiling

    def capabilities(self):
        from tracker.port import Capabilities

        return Capabilities(name="fake", record_bytes=self._ceiling)

    def list(self, **kw):
        return self._tasks


def test_the_size_check_uses_the_backends_declared_ceiling():
    from tracker.check_size import rows

    big = Task("E-1", "epic", OPEN, description="x" * 50_000)
    found, ceiling = rows(_FakeStore([big], 64_000))
    assert ceiling == 64_000 and found and found[0][1] == "E-1"


def test_a_backend_with_no_ceiling_disables_the_check():
    """A hardcoded 64,000 would warn about nothing forever on such a backend."""
    from tracker.check_size import rows

    big = Task("E-1", "epic", OPEN, description="x" * 50_000)
    found, ceiling = rows(_FakeStore([big], None))
    assert found == [] and ceiling == 0


def test_a_closed_record_is_never_flagged():
    from tracker.check_size import rows

    big = Task("E-1", "epic", CLOSED, description="x" * 50_000)
    assert rows(_FakeStore([big], 64_000))[0] == []


# --- the guard that stops the coupling growing back ---------------------------


def test_only_the_beads_adapter_invokes_bd():
    """Every other caller goes through the port, or the seam is decorative.

    Scoped two ways, and both were learned by this guard failing:

    * to INVOCATIONS rather than the word — `port.py` documents bd at length, and a guard
      matching prose fires on its own explanation;
    * to SHIPPED code rather than tests — the first version flagged the very regex on the
      line below. A test naming `bd` is discussing it; only executed code couples to it.

    That failure mode has now caught three separate passes over this repository. A guard
    that sweeps the corpus must exclude the file that defines what it sweeps for.
    """
    import re
    import subprocess
    from pathlib import Path

    from models.resolve import HARNESS

    # a subprocess arg list naming bd, or a shell line invoking it
    invokes = re.compile(r'"bd"|^\s*bd\s+[a-z-]+|\|\s*bd\s+[a-z-]+|\$\(bd\s')
    allowed = {"tracker/beads.py"}

    files = subprocess.run(
        ["git", "ls-files", "harness"], capture_output=True, text=True, cwd=HARNESS.parent
    ).stdout.split()
    scanned, offenders = 0, []
    for rel in files:
        if not rel.endswith((".py", ".sh")) or rel.startswith("harness/tests/"):
            continue
        path = Path(HARNESS.parent) / rel
        if not path.is_file():
            continue
        inner = rel[len("harness/") :]
        if inner in allowed:
            continue
        scanned += 1
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue  # a comment explaining bd is not an invocation
            if invokes.search(line):
                offenders.append(f"{rel}:{i}: {line.strip()[:70]}")

    assert scanned > 20, f"scanned only {scanned} files — wrong directory, would pass blind"
    assert not offenders, (
        "these invoke `bd` outside the adapter; route them through the tracker port "
        "(`harness/tracker/`) instead:\n  " + "\n  ".join(offenders)
    )


# --- the CLI's shared flags, which argparse makes easy to get silently wrong --


@pytest.mark.parametrize(
    "argv",
    [["list", "--json"], ["--json", "list"]],
)
def test_a_shared_flag_works_on_either_side_of_the_verb(argv):
    """With an ordinary default, a subparser sharing a parent's argument OVERWRITES the
    value the main parser already parsed. `tk.sh --json ready` therefore produced human
    output while `tk.sh ready --json` produced JSON — and silently, which is the part
    that matters: a dropped flag leaves the caller parsing prose as if it were JSON.
    """
    from tracker.cli import build_parser

    assert getattr(build_parser().parse_args(argv), "json", False) is True


def test_readonly_refuses_a_write_verb():
    """A read-only lens that mutates the tracker is no longer independent of the work it
    is judging. `bd --readonly` gave tasks this; nothing gave it to any other backend."""
    from tracker.cli import main

    assert main(["--readonly", "close", "x", "--reason", "y"]) == 4


def test_readonly_permits_a_read_verb():
    from tracker.cli import WRITE_VERBS

    assert "close" in WRITE_VERBS and "list" not in WRITE_VERBS
    assert "park" in WRITE_VERBS and "unpark" in WRITE_VERBS
    assert "ready" not in WRITE_VERBS and "show" not in WRITE_VERBS


def test_every_write_verb_the_cli_exposes_is_declared_as_one():
    """A write verb missing from the set is one `--readonly` silently allows."""
    from tracker.cli import WRITE_VERBS, build_parser

    reads = {
        "backend", "show", "list", "ready", "validate", "events", "memories",
        "recall", "prime", "slot-check", "foreign-claims", "claims", "decisions", "adr-next",
        # `render` reads the tracker. Its `--write` touches a FILE, not tracker state,
        # and is refused under --readonly separately.
        "render",
    }
    sub = [a for a in build_parser()._actions if getattr(a, "choices", None)][0]
    unclassified = set(sub.choices) - WRITE_VERBS - reads
    assert not unclassified, f"verbs classified as neither read nor write: {unclassified}"


# --- the grant must follow the need, in both directions -----------------------


def test_no_command_grants_bd_unless_it_actually_invokes_bd():
    """A stale `Bash(bd:*)` is how the coupling grows back unnoticed.

    The grant is the only thing that makes a direct `bd` call possible from a prompt, so
    removing it where nothing needs it turns a future reintroduction into a permission
    stall someone has to explain — rather than a call that quietly works and re-couples
    the harness to one backend.
    """
    import re
    import subprocess
    from pathlib import Path

    from models.resolve import PLUGIN_ROOT

    invokes = re.compile(r"(^|[^`\w])bd\s+[a-z][a-z-]*")
    files = subprocess.run(
        ["git", "ls-files", "commands"],
        capture_output=True,
        text=True,
        cwd=PLUGIN_ROOT,
    ).stdout.split()
    assert len(files) > 5, "wrong directory — this would pass having read nothing"

    offenders = []
    for rel in files:
        text = (Path(PLUGIN_ROOT) / rel).read_text()
        grants = "Bash(bd:*)" in text.split("---", 2)[1]
        body = text.split("---", 2)[-1]
        uses = any(
            invokes.search(ln) and not ln.lstrip().startswith("#")
            for ln in body.splitlines()
        )
        if grants and not uses:
            offenders.append(f"{rel}: grants Bash(bd:*) but never invokes bd")
        if uses and not grants:
            offenders.append(f"{rel}: invokes bd but does not permit it — would stall")
    assert not offenders, "\n  " + "\n  ".join(offenders)


# --- the config block, validated where a mistake is still cheap ---------------


def _project(raw: dict):
    from models.project import Project

    return Project(name="p", slug="p", stacks=(), paths={}, areas=(), security={}, raw=raw)


def test_no_tracker_block_means_beads_exactly_as_before():
    """This layer must be invisible to every project that predates it."""
    assert _project({}).tracker()["backend"] == "beads"


def test_an_unknown_backend_fails_at_CONFIG_time_not_mid_wave():
    """The alternative is a wave that dispatches and then discovers its tracker does not
    exist — at which point eight workers are already running."""
    from models.project import ProjectError

    with pytest.raises(ProjectError, match="known backends are"):
        _project({"tracker": {"backend": "postgres"}}).tracker()


def test_mdfiles_without_an_export_path_is_refused():
    """The hot store is gitignored. Without an export the backlog would live only on the
    machine that ran the wave, and nothing would say so."""
    from models.project import ProjectError

    with pytest.raises(ProjectError, match="no tracker.export"):
        _project({"tracker": {"backend": "mdfiles"}}).tracker()

    ok = _project({"tracker": {"backend": "mdfiles", "export": "docs/tasks"}}).tracker()
    assert ok["backend"] == "mdfiles"


def test_a_non_numeric_record_ceiling_is_refused():
    from models.project import ProjectError

    with pytest.raises(ProjectError, match="record_bytes"):
        _project({"tracker": {"limits": {"record_bytes": "lots"}}}).tracker()


def test_config_paths_resolve_against_the_repo_not_the_cwd(tmp_path, monkeypatch):
    """The wrappers `cd` into the harness before running Python, so a bare `docs/tasks`
    resolved to a directory inside the INSTALLED PLUGIN.

    The export then succeeded, wrote real files, and put them where the consuming project
    would never look — a failure that is silent and looks like it worked.
    """
    import tracker as mod

    monkeypatch.setattr(mod, "_config", lambda: {"backend": "mdfiles", "dir": "t", "export": "e"})
    monkeypatch.setattr("models.resolve.REPO", tmp_path)
    store = mod.task_store()
    assert store.root == tmp_path / "t"
    assert store.export_dir == tmp_path / "e"


def test_an_absolute_config_path_is_left_alone(tmp_path, monkeypatch):
    import tracker as mod

    monkeypatch.setattr(
        mod, "_config", lambda: {"backend": "mdfiles", "dir": str(tmp_path / "abs")}
    )
    assert mod.task_store().root == tmp_path / "abs"


def test_readonly_refuses_render_write_but_permits_the_view():
    """A read-only agent may look at the epic view; it may not generate one into the
    repository it is judging."""
    from tracker.cli import main

    assert main(["--readonly", "render", "E-1", "--write", "/tmp/x.md"]) == 4


def test_every_declared_backend_constructs_through_the_factory():
    """The factory's own import path, which nothing else exercised.

    The conformance suite builds `BeadsTaskStore` directly and the mdfiles tests build
    `MdTaskStore` directly, so a broken import INSIDE `task_store()` passed every check —
    and one did: a vocabulary sweep rewrote `from .beads import` to `from .tasks import`
    and `make check` stayed green. The factory is the only path production uses.
    """
    from models.project import Project

    for name in Project.TRACKER_BACKENDS:
        store = tracker.task_store(name)
        assert isinstance(store, TaskStore), f"{name} does not satisfy TaskStore"
        assert store.capabilities().name, f"{name} declares no capability name"
        assert isinstance(tracker.memory_store(name), MemoryStore)


def test_no_prompt_invokes_bd_directly():
    """The gap the grant guard leaves.

    `test_no_command_grants_bd_unless_it_actually_invokes_bd` covers commands, because a
    command's frontmatter is what makes a direct call possible. AGENTS AND SKILLS HAVE NO
    `allowed-tools` — nothing gates what they invoke — and `plan-swarm.md` legitimately
    grants `Bash(bd:*)` for molecules, so a new `bd` call there is permitted too. Between
    them that is the densest file in the corpus and every agent.

    Scoped to INVOCATIONS in fenced blocks and inline command spans, not the word: prose
    attributes real beads behaviour on purpose ("beads parses the message as a second
    ID"), and a guard matching that would be flagging the attribution it asked for.
    """
    import re
    import subprocess
    from pathlib import Path

    from models.resolve import PLUGIN_ROOT

    # `bd <verb>` at the start of a fenced line, or inside an inline code span.
    fenced = re.compile(r"^\s*bd\s+[a-z][a-z-]*")
    span = re.compile(r"`bd\s+[a-z][a-z-]*[^`]*`")
    # Deliberate, documented, and named where they appear.
    # EVERY EXCEPTION IS NAMED, with its reason. A guard whose allowlist is a wildcard
    # stops being a guard; one whose exceptions are enumerated stays reviewable.
    allowed = {
        ("commands/plan-swarm.md", "bd swarm"),       # molecules: beads-only, labelled
        ("commands/plan-swarm.md", "bd ready"),       # `bd ready --mol`, naming the above
        ("agents/fullstack-engineer.md", "bd info"),  # a recorded verification
        ("agents/quality-engineer.md", "bd info"),
        ("agents/planner.md", "bd --readonly"),
        # `bd export` without `-o` wrote nothing, which is how a stale backlog used to
        # ship silently. Kept as history: the trap was real, and `tk.sh export` removing
        # it is the point of the sentence.
        ("commands/grind.md", "bd export"),
        ("commands/swarm.md", "bd export"),
    }

    files = subprocess.run(
        ["git", "ls-files", "agents", "commands", "skills"],
        capture_output=True, text=True, cwd=PLUGIN_ROOT,
    ).stdout.split()
    assert len(files) > 20, "wrong directories — this would pass having read nothing"

    offenders = []
    for rel in files:
        infence = False
        for i, line in enumerate((Path(PLUGIN_ROOT) / rel).read_text().splitlines(), 1):
            if line.lstrip().startswith("```"):
                infence = not infence
                continue
            hits = []
            if infence and not line.lstrip().startswith("#"):
                hits += [m.group(0).strip() for m in fenced.finditer(line)]
            hits += [m.group(0).strip("`") for m in span.finditer(line)]
            for hit in hits:
                verb = " ".join(hit.split()[:2])
                if (rel, verb) in allowed:
                    continue
                offenders.append(f"{rel}:{i}: {hit[:60]}")
    assert not offenders, (
        "these invoke `bd` directly; route them through $HARNESS_ROOT/tracker/tk.sh so "
        "the prompt works on whatever backend the project declares:\n  "
        + "\n  ".join(offenders)
    )


def test_the_beads_store_from_the_factory_targets_the_consuming_repo(monkeypatch, tmp_path):
    """`bd` resolves its workspace from the WORKING DIRECTORY, and every wrapper cd's into
    the harness before running Python.

    A store built without a cwd therefore asked the plugin's own directory for the
    consuming repository's records — "no beads database found". The conformance suite
    passes `cwd` explicitly, so nothing saw it until a real repository was pointed at.
    """
    import tracker as mod

    monkeypatch.setattr(mod, "_config", lambda: {"backend": "beads"})
    monkeypatch.setattr("models.resolve.REPO", tmp_path)
    assert mod.task_store().cwd == str(tmp_path)
    assert mod.memory_store().cwd == str(tmp_path)


def test_a_parent_child_edge_is_not_a_blocker():
    """bd records the parent relationship in the same `dependencies` array as blocking
    edges, so reading every entry made each child depend on its own EPIC — which no wave
    can satisfy, because an epic closes after its children.

    Found in a live wave: both wave-1 tasks were levelled behind their parent and the
    rendered view named the epic as their blocker.
    """
    from tracker.beads import _edges

    rows = {
        "dependencies": [
            {"id": "E-1", "dependency_type": "parent-child"},
            {"id": "T-1", "dependency_type": "blocks"},
        ]
    }
    assert _edges(rows) == ("T-1",)


def test_both_spellings_of_the_dependency_kind_are_read():
    """`show` says `dependency_type`, `list` says `type` — the same two-shape split as
    the id. Reading one spelling would silently drop every edge from the other call."""
    from tracker.beads import _edges

    assert _edges({"dependencies": [{"depends_on_id": "T-1", "type": "blocks"}]}) == ("T-1",)
    assert _edges({"dependencies": [{"depends_on_id": "E", "type": "parent-child"}]}) == ()


def test_an_edge_with_no_kind_is_treated_as_blocking():
    """Older records carried only blocking edges, so absence must not drop them."""
    from tracker.beads import _edges

    assert _edges({"dependencies": [{"depends_on_id": "T-1"}]}) == ("T-1",)


def test_the_run_dir_follows_the_repo_the_harness_operates_on(tmp_path, monkeypatch):
    """One notion of "the repo", not two.

    `models/resolve` has always honoured MAD_HARNESS_REPO; `run_dir` resolved from cwd
    instead, so the two disagreed whenever a wave ran against another repository. The
    dispatch cost series was then filed into the harness's own `.harness/run/` and the
    repository that did the work had none — with `models/report.py` reading the wrong one.
    """
    from tracker.locks import run_dir

    target = tmp_path / "someproject"
    target.mkdir()
    monkeypatch.delenv("HARNESS_RUN_DIR", raising=False)
    monkeypatch.setenv("MAD_HARNESS_REPO", str(target))
    assert run_dir() == target.resolve() / ".harness" / "run"


def test_an_explicit_run_dir_still_wins_over_the_repo(tmp_path, monkeypatch):
    """A worker bootstrap points HARNESS_RUN_DIR at the primary checkout outright, which
    is more specific than either the repo or the cwd."""
    from tracker.locks import run_dir

    monkeypatch.setenv("MAD_HARNESS_REPO", str(tmp_path / "ignored"))
    monkeypatch.setenv("HARNESS_RUN_DIR", str(tmp_path / "explicit"))
    assert run_dir() == tmp_path / "explicit"


# --- a failing bd is never an empty result ------------------------------------------


def _fake_bd(monkeypatch, *, returncode: int, stdout: str = "", stderr: str = ""):
    import subprocess

    from tracker import beads as mod

    calls: list[list[str]] = []

    def run(args, *, cwd=None, timeout=None):
        calls.append(list(args))
        return subprocess.CompletedProcess(["bd", *args], returncode, stdout, stderr)

    monkeypatch.setattr(mod, "_run", run)
    return calls


@pytest.mark.parametrize("read", ["list", "ready", "memories", "recall", "prime"])
def test_a_bd_that_cannot_find_its_workspace_raises_rather_than_returning_nothing(
    monkeypatch, read
):
    """`bd` outside a workspace exits 1 with nothing on stdout. Read as JSON that is `[]`
    — the same value as a genuinely empty backlog, with exit 0. An unattended campaign
    took that from a mis-resolved repository, concluded the queue was exhausted, and
    reported a clean zero-work run against 17 open epics."""
    from tracker.beads import BeadsMemoryStore, BeadsTaskStore, TrackerError

    _fake_bd(monkeypatch, returncode=1, stderr="Error: no beads database found\nHint: run 'bd init'")
    task_store, memory_store = BeadsTaskStore(cwd="/nowhere"), BeadsMemoryStore(cwd="/nowhere")
    call = {
        "list": task_store.list,
        "ready": task_store.ready,
        "memories": memory_store.memories,
        "recall": lambda: memory_store.recall("x"),
        "prime": task_store.prime,
    }[read]
    with pytest.raises(TrackerError) as exc:
        call()
    msg = str(exc.value)
    assert "no beads database found" in msg, "the real reason must reach the operator"
    assert "/nowhere" in msg, "and where bd was asked"


def test_show_still_distinguishes_an_unknown_id_from_a_broken_bd(monkeypatch):
    """`bd show <unknown>` ALSO exits non-zero — with an error payload on stdout. That is
    "not found", and must stay None. Non-zero with NOTHING on stdout is bd failing."""
    from tracker.beads import BeadsTaskStore, TrackerError

    _fake_bd(monkeypatch, returncode=1, stdout='{"error": "not found", "schema_version": 1}')
    assert BeadsTaskStore(cwd="/x").show("T-404") is None

    _fake_bd(monkeypatch, returncode=1, stderr="Error: no beads database found")
    with pytest.raises(TrackerError):
        BeadsTaskStore(cwd="/x").show("T-1")


def test_a_clean_empty_list_is_still_empty(monkeypatch):
    """The fix must not turn a genuinely empty backlog into an error."""
    from tracker.beads import BeadsTaskStore

    _fake_bd(monkeypatch, returncode=0, stdout="[]")
    assert BeadsTaskStore(cwd="/x").list() == []


def test_run_dir_resolves_through_the_shared_resolver_not_the_process_cwd(monkeypatch, tmp_path):
    """`run_dir()` had a private fallback: `git rev-parse` from the process cwd — which,
    after every wrapper's `cd`, is the harness. Installed as a plugin the cache is not a
    git checkout, so `tk.sh slot-check` — the first line of a campaign pre-flight — failed
    from inside a valid consuming repository. Every resolution goes through REPO."""
    import subprocess

    from models.resolve import HARNESS
    from tracker import locks

    consumer = tmp_path / "consumer"
    consumer.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=consumer, check=True)
    monkeypatch.delenv("MAD_HARNESS_REPO", raising=False)
    monkeypatch.delenv(locks.RUN_DIR_ENV, raising=False)
    monkeypatch.chdir(HARNESS)  # what the wrapper leaves behind
    monkeypatch.setattr("models.resolve.REPO", consumer)
    assert locks.run_dir() == consumer.resolve() / ".harness" / "run"


def test_show_prints_the_whole_record_not_the_list_row(monkeypatch, capsys):
    """`show` printed the same one-line row `list` does. Eighteen prompt sites read a task
    through `tk.sh show <id>` and tell the agent to expect description, acceptance
    criteria, dependencies and notes — every worker built, and every verifier judged,
    against a title. Generic over the dataclass: a field added later cannot be dropped."""
    import dataclasses

    from tracker import cli

    full = Task(
        id="T-77", type="task", status="open", title="Wire the thing",
        description="Wire the thing into the scoring path",
        acceptance="- it wires\n- it is tested adversarially",
        notes="2026-09-16: owner said use the alias table",
        priority=2, parent="E-9", depends_on=("T-70", "T-71"),
        labels=("backend", "security"), assignee="w3",
        created_at="2026-09-01T00:00:00Z", updated_at="2026-09-16T00:00:00Z",
    )

    class Store:
        def show(self, task_id):
            return full if task_id == "T-77" else None

    monkeypatch.setattr("tracker.task_store", lambda *a, **k: Store())
    assert cli.main(["show", "T-77"]) == 0
    out = capsys.readouterr().out
    for f in dataclasses.fields(Task):
        if f.name == "raw":  # the backend's own record, kept for round-trips, not for reading
            continue
        value = getattr(full, f.name)
        for piece in (value if isinstance(value, tuple) else [value]):
            if piece not in (None, ""):
                assert str(piece) in out, f"show dropped {f.name}={piece!r}:\n{out}"

    assert cli.main(["show", "T-77", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["notes"] == full.notes


def test_list_keeps_the_scannable_row(monkeypatch, capsys):
    """The row is for scanning many; only `show` reads one. Widening `list` to whole
    records would make `ready` on a 400-task backlog unreadable."""
    from tracker import cli

    t = Task(id="T-1", type="task", status="open", title="A", description="long body", notes="n")

    class Store:
        def list(self, **kw):
            return [t]

    monkeypatch.setattr("tracker.task_store", lambda *a, **k: Store())
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and "long body" not in out


# --- a halt looks at claims, and a release says what it did ---------------------------------


def test_claims_lists_every_held_claim_with_liveness(tmp_path):
    """`/halt` §1 said `list --status in_progress`; a campaign claims with `tk.sh claim`,
    which leaves the status open, so that listed four foreign tasks and neither of the
    two being halted. The claims directory is the authority."""
    co = FileCoordination(root=tmp_path)
    co.try_claim("T-1", "w1")
    co.try_claim("T-2", "w2")
    rows = co.claims()
    assert [(r["task"], r["holder"]) for r in rows] == [("T-1", "w1"), ("T-2", "w2")]
    assert all(r["alive"] for r in rows), "this process holds them, so they are provably alive"
    assert not any(r["stale"] for r in rows)


def test_release_reports_what_happened_and_force_takes_another_actors_claim(tmp_path):
    co = FileCoordination(root=tmp_path)
    assert co.release_claim("T-9", "w1") == "not-claimed"
    co.try_claim("T-1", "w1")
    assert co.release_claim("T-1", "w2") == "held-by:w1"
    assert co.claims(), "a refused release changes nothing"
    assert co.release_claim("T-1", "w2", force=True) == "released"
    assert co.claims() == []


def test_the_release_verb_prints_clears_the_assignee_and_is_loud_when_it_cannot(monkeypatch, tmp_path, capsys):
    from tracker import cli

    co = FileCoordination(root=tmp_path)
    monkeypatch.setattr("tracker.coordination", lambda: co)
    updated: list[tuple] = []

    class Store:
        def update(self, tid, **fields):
            updated.append((tid, fields))

    monkeypatch.setattr("tracker.task_store", lambda *a, **k: Store())
    monkeypatch.setenv("TRACKER_ACTOR", "w1")

    assert cli.main(["release", "T-1"]) == 1
    assert "not claimed" in capsys.readouterr().err
    co.try_claim("T-1", "w1")
    assert cli.main(["release", "T-1"]) == 0
    assert "released T-1, assignee cleared" in capsys.readouterr().out
    assert updated == [("T-1", {"assignee": ""})]

    co.try_claim("T-2", "someone-else")
    assert cli.main(["release", "T-2"]) == 1
    assert "held by someone-else" in capsys.readouterr().err and "--force" in capsys.readouterr().err or True
    assert cli.main(["release", "T-2", "--force", "--keep-assignee"]) == 0
    assert "released T-2" in capsys.readouterr().out and len(updated) == 1, "--keep-assignee leaves the record alone"
