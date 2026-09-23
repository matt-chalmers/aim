"""0.10.27 — the agent-side primitives: each one a computation an agent was told to do by
hand, now code with a companion that makes it fail.

`tk.sh validate --paths` (the contention matrix above `graph.validate`), `staged.sh`
(`section` / `set-status` / `promote-adr`), the lens gate's mutation-log transport, a
reader's tracker read-only BY ENVIRONMENT, the security lens's invariants injected like
the card, `check-project-config.sh --stamp`, and `probe-worktree.sh`.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from models import steps
from models.resolve import HARNESS
from tracker.mdfiles import MdTaskStore

# --- contention: the matrix the planner computed by eye ------------------------------


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "shared.py").write_text("x = 1\n" * 50)
    (tmp_path / "src" / "big.py").write_text("y = 1\n" * 2000)
    (tmp_path / "src" / "own.py").write_text("z = 1\n")
    return tmp_path


def _epic_with(store, specs):
    """specs: [(title, description)] — all wave-1 leaves under one epic."""
    epic = store.create("The epic", type="epic")
    ids = [store.create(t, description=d, parent=epic) for t, d in specs]
    return epic, ids


def test_two_tasks_of_one_wave_naming_one_existing_path_is_an_edge_with_its_line_count(repo, tmp_path):
    from models.contention import contention_by_wave

    store = MdTaskStore(root=tmp_path / "tasks")
    epic, (a, b) = _epic_with(store, [("A", "edit src/shared.py"), ("B", "also edit src/shared.py")])
    doc = contention_by_wave(store, store.validate(epic), cwd=repo, megafile=1000)
    edges = doc["waves"][0]["edges"]
    assert len(edges) == 1
    assert set(edges[0]["tasks"]) == {a, b} and edges[0]["path"] == "src/shared.py"  # ids are hashes; the pair's order is the wave's
    assert edges[0]["lines"] == 50 and edges[0]["megafile"] is False


def test_a_megafile_is_flagged_and_disjoint_paths_make_no_edge(repo, tmp_path):
    from models.contention import contention_by_wave

    store = MdTaskStore(root=tmp_path / "tasks")
    epic, (a, b, c) = _epic_with(
        store, [("A", "edit src/big.py"), ("B", "edit src/big.py too"), ("C", "edit src/own.py only")]
    )
    doc = contention_by_wave(store, store.validate(epic), cwd=repo, megafile=1000)
    edges = doc["waves"][0]["edges"]
    assert [e["path"] for e in edges] == ["src/big.py"] and edges[0]["megafile"] is True
    assert all(c not in e["tasks"] for e in edges), "a task on its own file has no edge"
    assert doc["megafile_lines"] == 1000


def test_two_tasks_that_would_both_create_one_file_is_an_edge_of_its_own_kind(repo, tmp_path):
    from models.contention import contention_by_wave

    store = MdTaskStore(root=tmp_path / "tasks")
    epic, _ = _epic_with(store, [("A", "create src/new_service.py"), ("B", "create src/new_service.py as well")])
    doc = contention_by_wave(store, store.validate(epic), cwd=repo, megafile=1000)
    (edge,) = doc["waves"][0]["edges"]
    assert edge["both_create"] is True and edge["lines"] == 0


def test_tasks_serialised_by_a_dependency_are_in_different_waves_and_share_freely(repo, tmp_path):
    """The dependency graph already keeps them apart; the matrix is per wave."""
    from models.contention import contention_by_wave

    store = MdTaskStore(root=tmp_path / "tasks")
    epic, (a, b) = _epic_with(store, [("A", "edit src/shared.py"), ("B", "edit src/shared.py after A")])
    store.dep_add(b, a)
    doc = contention_by_wave(store, store.validate(epic), cwd=repo, megafile=1000)
    assert len(doc["waves"]) == 2 and all(not w["edges"] for w in doc["waves"])


def test_the_cli_reports_the_edges_on_stderr_and_in_the_json(repo, tmp_path, monkeypatch):
    """`validate --paths` is what the planner and apply-plan call; its stderr line is the
    one a reader sees without opening the JSON."""
    import io
    import json
    import sys as _sys

    monkeypatch.chdir(repo)
    monkeypatch.setenv("MAD_HARNESS_CALLER_PWD", str(repo))
    from tracker import cli

    store = MdTaskStore(root=tmp_path / "tasks")
    epic, _ = _epic_with(store, [("A", "edit src/shared.py"), ("B", "edit src/shared.py")])
    monkeypatch.setattr(cli, "task_store", lambda *a, **k: store, raising=False)
    import tracker as trk

    monkeypatch.setattr(trk, "task_store", lambda *a, **k: store)
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(_sys, "stdout", out)
    monkeypatch.setattr(_sys, "stderr", err)
    monkeypatch.setattr("models.contention.contention_by_wave", lambda s, v, **k: {"megafile_lines": 1000, "waves": [{"index": 1, "tasks": [], "edges": [{"tasks": ["a", "b"], "path": "src/shared.py", "megafile": False, "lines": 50}]}], "note": ""})
    rc = cli.main(["validate", epic, "--paths"])
    assert rc == 0
    assert "CONTENTION: 1 edge(s)" in err.getvalue()
    assert json.loads(out.getvalue())["contention"]["waves"][0]["edges"][0]["path"] == "src/shared.py"


# --- staged.sh: the spec-editor's three edits ---------------------------------------


PROPOSAL = """---
status: draft
lands_in: [docs/features/x.md]
---
# Proposal

The `## Acceptance criteria` heading is discussed here, in prose, before it appears.

## Base assumed

- docs/features/x.md said A.

## Acceptance criteria

- [ ] one
- [ ] two

### Scope B

- [ ] three

## Fold-in routing

| a | b |
"""


def test_section_is_line_anchored_and_stops_at_the_next_heading_of_the_same_level():
    from tracker.staging import section

    body = section(PROPOSAL, "Acceptance criteria")
    assert body is not None
    assert body.startswith("- [ ] one") and "- [ ] three" in body, "a ### subsection belongs to its ## section"
    assert "Fold-in routing" not in body and "| a | b |" not in body
    assert "discussed here" not in body, "the mention inside prose is not the heading"


def test_a_missing_heading_is_none_and_a_duplicated_one_is_an_error():
    from tracker.staging import section

    assert section(PROPOSAL, "Nope") is None
    doubled = PROPOSAL + "\n## Acceptance criteria\n\n- [ ] four\n"
    with pytest.raises(ValueError, match="appears 2 times"):
        section(doubled, "Acceptance criteria")


def test_set_status_flips_in_place_and_adds_keys_and_a_file_without_frontmatter_gets_one(tmp_path):
    from tracker.staging import set_status

    p = tmp_path / "proposal.md"
    p.write_text(PROPOSAL)
    set_status(p, "folded-in", folded_in="2026-09-20")
    text = p.read_text()
    assert text.startswith("---\nstatus: folded-in\nlands_in: [docs/features/x.md]\nfolded_in: 2026-09-20\n---\n")
    assert "# Proposal" in text and text.count("---\n") == 2
    bare = tmp_path / "bare.md"
    bare.write_text("# Bare\n")
    set_status(bare, "draft")
    assert bare.read_text() == "---\nstatus: draft\n---\n# Bare\n"


def test_promote_adr_moves_to_the_next_number_and_fills_status_and_decision(tmp_path):
    from tracker.staging import promote_adr

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    adrs = tmp_path / "docs" / "adr"
    adrs.mkdir(parents=True)
    (adrs / "0007-old.md").write_text("# ADR-0007: old\n")
    staged = tmp_path / "docs" / "proposed" / "e-1-thing"
    staged.mkdir(parents=True)
    draft = staged / "adr-draft-1-which-store.md"
    draft.write_text(
        "# ADR-XXXX: Which store?\n\n**Status**: Proposed — awaiting owner decision\n**Epic**: e-1\n\n"
        "> **Draft** — staged; not an ADR yet, and nothing may cite it as settled.\n\n## Context\n\nC\n\n## Decision\n\n(open)\n\n## Consequences\n\nK\n"
    )
    dest = promote_adr(draft, adrs, decision="Use the embedded one.", cwd=tmp_path)
    assert dest == adrs / "0008-which-store.md" and dest.is_file() and not draft.exists()
    text = dest.read_text()
    assert text.startswith("# ADR-0008: Which store?")
    assert "**Status**: Accepted" in text and "Proposed" not in text
    assert "> **Draft**" not in text
    assert "## Decision\n\nUse the embedded one.\n\n## Consequences" in text
    assert "(open)" not in text


def test_the_staged_wrapper_is_executable_and_runs_the_module():
    sh = Path(HARNESS) / "swarm" / "staged.sh"
    assert sh.exists() and os.access(sh, os.X_OK), "a wrapper nobody can execute is prose"
    assert "python -m models.staged" in sh.read_text()


def test_staged_cli_section_exits_1_on_a_missing_heading_and_2_on_a_duplicate(tmp_path, capsys):
    from models import staged

    p = tmp_path / "p.md"
    p.write_text(PROPOSAL)
    assert staged.main(["section", str(p), "Acceptance criteria"]) == 0
    assert "- [ ] one" in capsys.readouterr().out
    assert staged.main(["section", str(p), "Nope"]) == 1
    assert "report it, do not guess" in capsys.readouterr().err
    p.write_text(PROPOSAL + "\n## Acceptance criteria\n\nagain\n")
    assert staged.main(["section", str(p), "Acceptance criteria"]) == 2


def test_staged_cli_set_status_folded_in_dates_folded_in_by_default(tmp_path, capsys):
    from datetime import date

    from models import staged

    p = tmp_path / "p.md"
    p.write_text(PROPOSAL)
    assert staged.main(["set-status", str(p), "folded-in"]) == 0
    assert f"folded_in: {date.today().isoformat()}" in p.read_text()
    assert staged.main(["set-status", str(p), "draft", "not-a-pair"]) == 2


# --- the mutation log has a transport to L2 -------------------------------------------

_INFO = {"brief": "/b/T-1/brief.md", "root": "/b/T-1", "diff_root": "/bd/T-1", "artefacts": "/bd/T-1/artefacts.md", "commit": "a" * 40, "files": 1,
         "l4": {"fires": False, "why": [], "surface": "", "touched_security_path": False}}


def test_the_lens_gate_finds_the_newest_mutation_log_in_the_checkout_and_names_it_to_l2(tmp_path):
    import time

    from models import lens_gate

    mut = tmp_path / ".harness" / "run" / "mut"
    (mut / "a-mut").mkdir(parents=True)
    (mut / "b-mut").mkdir(parents=True)
    older = mut / "a-mut" / "a-mutants.txt"
    older.write_text("old")
    time.sleep(0.02)
    newer = mut / "b-mut" / "b-mutants.txt"
    newer.write_text("new")
    os.utime(older, (1, 1))
    assert lens_gate.mutation_log(str(tmp_path)) == newer
    pr = lens_gate.prompts("T-1", "abc", _INFO, None, None, "--l4 always", newer)
    assert str(newer) in pr["L2"] and "spot-re-run THREE" in pr["L2"]


def test_no_mutation_log_is_said_to_l2_as_a_finding_not_omitted(tmp_path):
    from models import lens_gate

    assert lens_gate.mutation_log(str(tmp_path)) is None
    pr = lens_gate.prompts("T-1", "abc", _INFO, None, None, "--l4 always", None)
    assert "No `mutate.sh` log was found" in pr["L2"] and "no mutation evidence" in pr["L2"] and "cannot author a mutations file" in pr["L2"]


# --- a reader's tracker is read-only by environment -----------------------------------


def test_a_reader_gets_tracker_readonly_and_a_writer_never_inherits_it(monkeypatch):
    from models.dispatch import build_env
    from models.resolve import resolve

    monkeypatch.setenv("TRACKER_READONLY", "1")  # a dispatcher that is itself a reader
    reader = build_env(resolve("verifier"))
    writer = build_env(resolve("fullstack-engineer"))
    assert reader["TRACKER_READONLY"] == "1"
    assert "TRACKER_READONLY" not in writer, "a writer dispatched by a reader must still be able to close its task"


def test_tk_honours_the_environment_as_readonly(monkeypatch, tmp_path, capsys):
    """The variable is the flag: a write verb under it is refused exactly as under --readonly."""
    import tracker as trk
    from tracker import cli

    store = MdTaskStore(root=tmp_path / "tasks")
    monkeypatch.setattr(trk, "task_store", lambda *a, **k: store)
    monkeypatch.setenv("TRACKER_READONLY", "1")
    rc = cli.main(["create", "A task"])
    assert rc == 4, "read-only exits 4"
    assert not store.list(), "nothing was created"
    monkeypatch.delenv("TRACKER_READONLY")
    assert cli.main(["create", "A task"]) == 0 and len(store.list()) == 1


# --- the security lens gets the declared invariants like the card ---------------------


def test_the_security_lens_prompt_carries_every_declared_invariant_verbatim_and_no_other_agent_does():
    from models.context import SECURITY_LENS, render_invariants
    from models.dispatch import with_context
    from models.project import load

    rules = load().invariants()
    assert rules, "this repository declares invariants; the test needs at least one"
    text = with_context("PROMPT", None, agent=SECURITY_LENS)
    assert "## Declared security invariants" in text
    for i, r in enumerate(rules, 1):
        assert f"{i}. {r}" in text
    assert "Declared security invariants" not in with_context("PROMPT", None, agent="verifier")
    assert render_invariants(None) == ""


def test_a_project_declaring_no_invariants_is_told_so_not_left_searching():
    from models.context import SECURITY_LENS, render_invariants
    from models.project import Project

    p = Project(name="n", slug="n", stacks=(), paths={}, areas=(), security={}, raw={})
    text = render_invariants(SECURITY_LENS, p)
    assert "declares none" in text and "do not go looking" in text


def test_the_security_lens_named_in_code_is_an_agent_that_ships():
    from models.context import SECURITY_LENS
    from models.resolve import _prompts_dir

    assert (_prompts_dir("agents") / f"{SECURITY_LENS}.md").is_file()


def test_the_lens_prose_no_longer_asks_the_agent_to_find_the_list():
    from models.resolve import _prompts_dir

    text = (_prompts_dir("agents") / "verifier-security.md").read_text()
    assert "Read them from the config" not in text
    assert "appends that list to your prompt" in text


# --- check-project-config.sh --stamp ---------------------------------------------------


def test_stamp_rewrites_the_version_in_place_keeping_everything_else(tmp_path):
    from models.check_project import stamp

    cfg = tmp_path / "harness.yaml"
    cfg.write_text("name: X\nslug: x\n\nharness:\n  version: 0.9.1   \n  other: keep\n\nstacks: [a]\n")
    assert stamp(cfg, "0.10.27") == "re-stamped harness.version: 0.10.27"
    assert cfg.read_text() == "name: X\nslug: x\n\nharness:\n  version: 0.10.27\n  other: keep\n\nstacks: [a]\n"


def test_stamp_adds_the_line_to_a_harness_block_without_one_and_a_block_to_a_config_without_one(tmp_path):
    from models.check_project import stamp

    cfg = tmp_path / "harness.yaml"
    cfg.write_text("name: X\nharness:\n  other: keep\nstacks: [a]\n")
    stamp(cfg, "1.2.3")
    assert "harness:\n  other: keep\n  version: 1.2.3\nstacks: [a]\n" in cfg.read_text()
    cfg.write_text("name: X\nstacks: [a]\n")
    assert stamp(cfg, "1.2.3").startswith("stamped")
    assert cfg.read_text().endswith("\n\nharness:\n  version: 1.2.3\n")


def test_stamp_does_not_touch_a_version_key_outside_the_harness_block(tmp_path):
    from models.check_project import stamp

    cfg = tmp_path / "harness.yaml"
    cfg.write_text("api:\n  version: 3\nharness:\n  version: 0.1.0\n")
    stamp(cfg, "9.9.9")
    assert cfg.read_text() == "api:\n  version: 3\nharness:\n  version: 9.9.9\n"


def test_the_installed_version_is_what_gets_stamped(tmp_path, monkeypatch, capsys):
    """Read from the manifest, never typed — the whole reason the flag exists."""
    from models import check_project
    from models.project import plugin_version

    cfg = tmp_path / "harness.yaml"
    cfg.write_text("harness:\n  version: 0.0.1\n")
    monkeypatch.setattr(check_project, "PROJECT_FILE", cfg)
    monkeypatch.setattr(check_project, "load", lambda: (_ for _ in ()).throw(check_project.ProjectError("stop here")))
    check_project.main(["--stamp"])
    assert f"version: {plugin_version()}" in cfg.read_text()
    assert "re-stamped" in capsys.readouterr().out


# --- probe-worktree.sh --------------------------------------------------------------------


class Runner:
    """Answers by the first argv token that matters; records order. `.swarm-env` is written
    by the fake init so the probe reads a real file."""

    def __init__(self, wt_env: str | None, fail: str | None = None):
        self.calls: list[list[str]] = []
        self.wt_env, self.fail = wt_env, fail

    def __call__(self, argv, cwd=None, capture_output=True, text=True, timeout=None):
        self.calls.append(list(argv))
        rc, out, err = 0, "", ""
        if argv[0] == "git" and argv[1] == "worktree" and argv[2] == "add":
            Path(argv[4]).mkdir(parents=True, exist_ok=True)  # git worktree add --detach <path> HEAD
        elif argv[0].endswith("swarm-worktree-init.sh"):
            if self.fail == "init":
                rc, err = 1, "!!  refusing"
            elif self.wt_env is not None:
                Path(cwd, ".swarm-env").write_text(self.wt_env)
                out = "==> wrote .swarm-env\n==> first run for x:\n    run.sh test"
        elif argv[0] == "bash":
            # A real shell, on the fake file — these are the checks that matter.
            proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
            rc, out, err = proc.returncode, proc.stdout, proc.stderr
        elif argv[0] == "git" and argv[1] == "worktree" and argv[2] == "remove":
            if self.fail == "remove":
                rc, err = 1, "cannot remove"
        return subprocess.CompletedProcess(argv, rc, out, err)


def _project():
    from models.project import Project, Stack

    st = Stack(name="py", description="", root="", detect_any=(), dependency_dir=".venv", bootstrap={}, env={"DB_NAME": "{slug}_w{worker}"}, commands={})
    return Project(name="P", slug="proj", stacks=(st,), paths={}, areas=(), security={}, raw={"lanes": {"backend": {"cap": 2}}})


GOOD = "export TRACKER_ACTOR=swarm-w1\nexport BEADS_ACTOR=swarm-w1\nexport DB_NAME=proj_w1\nexport SWARM_LANE=backend\n"


def test_the_probe_creates_inits_sources_checks_and_removes_in_that_order(tmp_path):
    from models import probe_worktree as mod

    r = Runner(GOOD)
    results, code = mod.run(lane="backend", worker=1, project=_project(), runner=r, cwd=str(tmp_path), root=tmp_path / "wts")
    assert code == 0, steps.render(results)
    names = [x.name for x in results]
    assert names == ["git worktree add", "swarm-worktree-init.sh", "source .swarm-env", "TRACKER_ACTOR", "SWARM_LANE", "DB_NAME", "git worktree remove"]
    assert [c[:3] for c in r.calls][0] == ["git", "worktree", "add"] and r.calls[-1][:3] == ["git", "worktree", "remove"]
    assert r.calls[1][0].endswith("swarm-worktree-init.sh") and r.calls[1][1:] == ["1", "backend"]


def test_a_value_that_reads_fine_but_fails_a_real_shell_is_caught_by_sourcing(tmp_path):
    from models import probe_worktree as mod

    # Parses under `bash -n`; fails only when a real shell expands it — the class of defect
    # the eye reads as fine.
    r = Runner("export DB_NAME=proj_w1\nexport SWARM_LANE=${LANE_FROM_NOWHERE:?not set}\n")
    results, code = mod.run(lane="backend", project=_project(), runner=r, cwd=str(tmp_path), root=tmp_path / "wts")
    assert code == 1
    src = next(x for x in results if x.name == "source .swarm-env")
    assert src.status == steps.FAIL
    assert results[-1].name == "git worktree remove" and results[-1].status == steps.OK, "removed even after the failure"


def test_a_shared_database_is_a_failure_that_names_the_variable(tmp_path):
    from models import probe_worktree as mod

    r = Runner(GOOD.replace("proj_w1", "proj_shared"))
    results, code = mod.run(lane="backend", project=_project(), runner=r, cwd=str(tmp_path), root=tmp_path / "wts")
    assert code == 1
    db = next(x for x in results if x.name == "DB_NAME")
    assert db.status == steps.FAIL and "not per-worker" in db.detail


def test_a_project_with_no_per_worker_variable_is_told_not_passed_silently(tmp_path):
    from models import probe_worktree as mod
    from models.project import Project

    p = Project(name="P", slug="proj", stacks=(), paths={}, areas=(), security={}, raw={})
    r = Runner("export TRACKER_ACTOR=swarm-w1\nexport SWARM_LANE=backend\n")
    results, code = mod.run(lane="backend", project=p, runner=r, cwd=str(tmp_path), root=tmp_path / "wts")
    assert code == 0
    info = next(x for x in results if x.name == "per-worker variable")
    assert info.status == steps.INFO and "no stack declares one" in info.detail


def test_an_init_that_refuses_stops_the_probe_and_the_worktree_is_still_removed(tmp_path):
    from models import probe_worktree as mod

    r = Runner(GOOD, fail="init")
    results, code = mod.run(lane="backend", project=_project(), runner=r, cwd=str(tmp_path), root=tmp_path / "wts")
    assert code == 1
    assert [x.name for x in results] == ["git worktree add", "swarm-worktree-init.sh", "git worktree remove"]
    assert not any(c[0] == "bash" for c in r.calls), "nothing sourced after a failed init"


def test_keep_leaves_the_worktree_and_prints_how_to_remove_it_and_a_failed_removal_is_a_failure(tmp_path):
    from models import probe_worktree as mod

    r = Runner(GOOD)
    results, code = mod.run(lane="backend", project=_project(), runner=r, keep=True, cwd=str(tmp_path), root=tmp_path / "wts")
    assert code == 0 and results[-1].name == "worktree kept" and "git worktree remove --force" in results[-1].detail
    assert not any(c[:3] == ["git", "worktree", "remove"] for c in r.calls)
    r = Runner(GOOD, fail="remove")
    results, code = mod.run(lane="backend", project=_project(), runner=r, cwd=str(tmp_path), root=tmp_path / "wts")
    assert code == 1 and results[-1].status == steps.FAIL


def test_an_undeclared_lane_is_usage_and_creates_nothing(tmp_path):
    from models import probe_worktree as mod

    r = Runner(GOOD)
    results, code = mod.run(lane="nope", project=_project(), runner=r, cwd=str(tmp_path), root=tmp_path / "wts")
    assert code == 2 and not r.calls and "backend" in results[0].detail


def test_the_probe_wrapper_is_executable_and_runs_the_module():
    sh = Path(HARNESS) / "swarm" / "probe-worktree.sh"
    assert sh.exists() and os.access(sh, os.X_OK), "a wrapper nobody can execute is prose"
    text = sh.read_text()
    assert "MAD_HARNESS_CALLER_PWD" in text and "python -m models.probe_worktree" in text


def test_the_probe_names_an_init_script_that_ships():
    from models import probe_worktree as mod

    assert mod.INIT.is_file() and os.access(mod.INIT, os.X_OK)


def test_a_bootstrap_strategy_of_none_restores_nothing_and_says_so(tmp_path):
    """What the probe's first real run found: the harness's own stack said `symlink` to a
    directory that never existed, and every worker worktree on it refused to init."""
    from models.project import Project, Stack
    from models.worker import bootstrap

    st = Stack(name="self", description="", root="harness", detect_any=(), dependency_dir=".venv-none", bootstrap={"strategy": "none"}, env={}, commands={})
    p = Project(name="P", slug="p", stacks=(st,), paths={}, areas=(), security={}, raw={})
    wt = tmp_path / "wt"
    wt.mkdir()
    log = bootstrap(wt, tmp_path / "main", p)
    assert log == ["==> nothing to restore (self)"]
    assert not (wt / "harness" / ".venv-none").exists()


def test_the_harness_own_stack_declares_none():
    import yaml

    from models.resolve import HARNESS

    st = yaml.safe_load((Path(HARNESS) / "stacks" / "python-uv-selftest.yaml").read_text())
    assert st["bootstrap"]["strategy"] == "none"
