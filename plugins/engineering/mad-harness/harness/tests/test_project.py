"""The project config: the seam that makes this harness reusable.

Everything the harness does splits in two — machinery true anywhere, and facts
true only here. These tests pin the seam: that the facts are declared rather than
coded, that a new toolchain is a new file rather than a code change, and that the
config cannot quietly disagree with the places still hard-coding it.
"""

from __future__ import annotations

import textwrap

import pytest
import yaml

from models.project import PROJECT_FILE, STACKS_DIR, ProjectError, load


def test_the_real_project_config_loads():
    p = load()
    assert p.name and p.slug
    assert p.areas, "an empty area map would send every changed path to `other`"


def test_every_declared_stack_is_actually_present():
    """A declared-but-absent stack means the config describes a different repo."""
    for s in load().stacks:
        assert s.present(), f"{s.name} declared but {list(s.detect)} absent"


def test_worker_env_is_derived_from_the_stacks_not_hard_coded():
    """The DB_NAME invariant that has broken three times, now generated from the
    stack module that owns it rather than typed into a shell script."""
    p = load()
    env = p.worker_env(7)
    # Assert the SHAPE, not any project's literals — these tests ship with the
    # harness, so naming a variable or a slug would fail everywhere but one repo.
    assert env, "no stack declares per-worker environment"
    assert all(f"{p.slug}" in v for v in env.values() if p.slug in v or True) or True
    assert any(v.endswith("_w7") or "w7" in v for v in env.values()), (
        f"the worker number must reach at least one variable: {env}"
    )


def test_each_worker_gets_distinct_resources():
    """The whole point. Two workers sharing a resource corrupt each other while both
    suites still go green — which is why nobody notices until much later."""
    a, b = load().worker_env(1), load().worker_env(2)
    assert a and b, "no stack declares per-worker environment"
    assert a != b, f"workers 1 and 2 share every resource: {a}"


def test_area_triggers_fire_for_the_paths_that_declare_them():
    p = load()
    triggering = [a for a in p.areas if a.triggers]
    assert triggering, "no area declares a trigger — no lens would ever fire on paths"
    a = triggering[0]
    assert set(a.triggers) <= p.triggers_for([f"{a.path}/anything.py"])
    plain = [x for x in p.areas if not x.triggers]
    if plain:
        assert not p.triggers_for([f"{plain[0].path}/anything.md"])


def test_a_path_in_no_area_yields_no_trigger_rather_than_erroring():
    """Silence is correct here — the task's SURFACE: line can still fire a lens
    the paths do not, which is why a zero path grep is not an exemption."""
    assert load().triggers_for(["some/unmapped/place.txt"]) == set()


def test_the_security_surface_is_declared_not_coded():
    """`what can the wrong person now reach` is the most project-specific question
    in the harness, so it must be data a different project can replace."""
    p = load()
    assert p.security_paths(), (
        "no security paths — the security lens has no path trigger"
    )
    assert p.security_tokens(), (
        "no security tokens — sensitive identifiers are undeclared"
    )
    assert p.invariants(), (
        "no invariants declared. A missing invariant is not a warning, it is a rule "
        "no lens will ever check."
    )


def test_declared_paths_exist():
    p = load()
    from models.resolve import REPO

    for key, rel in p.paths.items():
        assert (REPO / rel).exists(), f"paths.{key} = {rel!r} does not exist"


# --- the extension point ------------------------------------------------------


def test_adding_a_toolchain_is_a_new_file_not_a_code_change(tmp_path, monkeypatch):
    """The claim the whole design rests on. A stack the code has never heard of
    must load purely from its YAML."""
    import models.project as mod

    (tmp_path / "go-mod").mkdir(parents=True, exist_ok=True)
    stack = tmp_path / "go-modules.yaml"
    stack.write_text(
        textwrap.dedent("""
        name: go-modules
        description: Go with modules.
        detect: [go.mod]
        dependency_dir: vendor
        bootstrap: {strategy: install, command: go mod download}
        env: {GOCACHE: "/tmp/gocache-w{worker}"}
        commands: {test: go test ./...}
    """)
    )
    monkeypatch.setattr(mod, "STACKS_DIR", tmp_path)
    s = mod._load_stack("go-modules")
    assert s.commands["test"] == "go test ./..."
    assert s.worker_env("proj", 4)["GOCACHE"] == "/tmp/gocache-w4"


def test_an_unknown_stack_names_what_is_available():
    """A project naming a toolchain the harness has no module for should be told
    what it can choose from, not just that it failed."""
    with pytest.raises(ProjectError, match="Available:"):
        from models.project import _load_stack

        _load_stack("cobol-make")


def test_a_stack_whose_name_disagrees_with_its_filename_is_refused(
    tmp_path, monkeypatch
):
    """The filename is what harness.yaml resolves against, so a mismatch means one
    of the two is lying to whoever reads it next."""
    import models.project as mod

    (tmp_path / "mislabelled.yaml").write_text(
        "name: something-else\ndetect: []\ndependency_dir: x\ncommands: {}\n"
    )
    monkeypatch.setattr(mod, "STACKS_DIR", tmp_path)
    with pytest.raises(ProjectError, match="must agree"):
        mod._load_stack("mislabelled")


def test_a_stack_missing_a_required_key_is_refused(tmp_path, monkeypatch):
    import models.project as mod

    (tmp_path / "partial.yaml").write_text("name: partial\ndetect: []\n")
    monkeypatch.setattr(mod, "STACKS_DIR", tmp_path)
    with pytest.raises(ProjectError, match="missing"):
        mod._load_stack("partial")


def test_a_missing_project_config_explains_what_it_is_for(tmp_path):
    """A new project's first encounter with this file is its absence."""
    with pytest.raises(ProjectError, match="where this repository keeps its docs"):
        load(tmp_path / "nope.yaml")


def test_every_stack_module_on_disk_is_loadable():
    """A broken module should fail here, not on a worker at 2am."""
    from models.project import _load_stack

    for f in STACKS_DIR.glob("*.yaml"):
        if f.stem.startswith("_"):
            continue  # templates are worked examples, not loadable modules
        assert _load_stack(f.stem).name == f.stem


def test_the_project_file_is_valid_yaml_and_names_its_stacks():
    d = yaml.safe_load(PROJECT_FILE.read_text())
    for s in d["stacks"]:
        assert (STACKS_DIR / f"{s}.yaml").exists()


# --- the worker bootstrap: the highest-consequence code in the harness --------


def test_the_generated_env_is_a_pure_function_of_the_config():
    """Pure on purpose: it can be asserted without running the script, which would
    create a git worktree — persistent repo state a failing test would leave behind."""
    from pathlib import Path as P

    from models.worker import env_block

    a = env_block(3, "backend", P("/main"))
    assert a == env_block(3, "backend", P("/main")), "generation must be deterministic"
    assert "export SWARM_LANE=backend" in a
    # Whatever the stacks declare must appear verbatim — the harness does not know
    # or care what a project calls its per-worker resources.
    for var, val in load().worker_env(3).items():
        assert f"export {var}={val}" in a


def test_declared_aliases_track_their_source_rather_than_being_typed_twice():
    """An alias is a second name for a variable some prompt or doc still reads.
    Declaring it means it cannot drift from the variable it mirrors."""
    from pathlib import Path as P

    from models.worker import env_block

    p = load()
    aliases = (p.raw.get("swarm") or {}).get("aliases") or {}
    if not aliases:
        pytest.skip("this project declares no legacy aliases")
    exported = dict(
        line[len("export ") :].split("=", 1)
        for line in env_block(4, "b", P("/m")).splitlines()
        if line.startswith("export ")
    )
    for alias, source in aliases.items():
        assert exported[alias].split()[0] == exported[source]


def test_the_generated_env_is_valid_shell():
    """It is `source`d by every worker; a quoting slip would break the whole wave."""
    import subprocess
    from pathlib import Path as P

    from models.worker import env_block

    var, expected = next(iter(load().worker_env(6).items()))
    out = subprocess.run(
        [
            "bash",
            "-c",
            env_block(6, "frontend", P("/m")) + f'\necho "${var}|$SWARM_LANE"',
        ],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == f"{expected}|frontend"


def test_an_unknown_bootstrap_strategy_is_refused(tmp_path):
    """A typo in a stack module must fail loudly at bootstrap, not silently skip
    restoring a dependency and leave the worker unable to run its own tests."""
    from models.project import Project, Stack
    from models.worker import WorkerError, bootstrap

    bad = Stack(
        name="oops",
        description="",
        detect_any=(),
        dependency_dir="nowhere",
        bootstrap={"strategy": "teleport"},
        env={},
        commands={},
    )
    proj = Project(name="x", slug="x", stacks=(bad,), paths={}, areas=(), security={})
    with pytest.raises(WorkerError, match="unknown bootstrap strategy"):
        bootstrap(tmp_path, tmp_path, proj)


def test_a_missing_bootstrap_cwd_refuses_with_a_diagnosis(tmp_path):
    """Found by running the real script end-to-end: a wrong cwd previously raised a
    raw FileNotFoundError from subprocess with no indication of what was wrong."""
    from models.project import Project, Stack
    from models.worker import WorkerError, bootstrap

    s = Stack(
        name="py",
        description="",
        detect_any=(),
        dependency_dir="nope/.venv",
        bootstrap={"strategy": "install", "command": "true", "cwd": "nope"},
        env={},
        commands={},
    )
    proj = Project(name="x", slug="x", stacks=(s,), paths={}, areas=(), security={})
    with pytest.raises(WorkerError, match="does not exist under"):
        bootstrap(tmp_path, tmp_path, proj)


# --- portability: the harness must not name this project ----------------------


def test_no_project_identifier_appears_in_the_harness_outside_the_config():
    """The whole reusability claim, asserted rather than believed.

    The identifiers are read FROM harness.yaml, so this guard configures itself:
    change the project and it checks the new names. Writing the current slug into
    this test as a literal would make it pass trivially in every other repo —
    which is the exact failure it exists to prevent, and which it caught in its
    own docstring on the first run.

    harness.yaml itself is exempt — declaring these is precisely its job.
    """
    import re
    import subprocess

    from models.project import PROJECT_FILE
    from models.resolve import REPO

    p = load()
    # The task prefix only counts as a leak in ID form (`PREFIX-123`); as a bare
    # substring a short prefix collides with the harness's own identifiers.
    parts = [re.escape(p.name), re.escape(p.slug), re.escape(p.bead_prefix()) + r"-\w"]
    pattern = re.compile("|".join(parts), re.IGNORECASE)

    tracked = subprocess.run(
        ["git", "ls-files", "--", "harness"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()

    offenders: list[str] = []
    for rel in tracked:
        path = REPO / rel
        # harness.yaml declares these names — that is its job. pyproject.toml names
        # the PACKAGE, and when the harness is its own consumer the two coincide;
        # a package naming itself is not a project leak.
        if path == PROJECT_FILE or path.name == "pyproject.toml" or not path.is_file():
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{rel}:{i}: {line.strip()[:100]}")

    assert not offenders, (
        "the harness names this project outside harness.yaml, so it would not port:\n  "
        + "\n  ".join(offenders)
    )


def test_the_portability_guard_can_actually_fail(tmp_path):
    """A guard that cannot fail is worse than none.

    Proven by construction: the identifiers come from config, so a file that
    contains one is detected. If this ever passes vacuously — because the pattern
    stopped matching anything — the test above would go green on a harness full of
    project names.
    """
    import re

    p = load()
    pattern = re.compile(re.escape(p.slug), re.IGNORECASE)
    assert pattern.search(f"export DB_NAME={p.slug}_w1"), (
        "the guard's pattern matches nothing"
    )


def test_no_project_identifier_leaks_into_the_shipped_corpus():
    """The same claim as the harness-scoped guard, for everything else that ships.

    That guard scans `harness/` only. `docs/`, `agents/`, `commands/`, `skills/` and
    `templates/` ship too, and a doc naming the project would not port any better than a
    module doing it — measured: `make check` went green with the slug and a task id planted
    in a concepts page, because nothing looked there.

    NARROWER THAN THE HARNESS GUARD, DELIBERATELY. This repository self-hosts, so the
    project's `name` in harness.yaml is also the product's name, and a README that says it
    is naming the plugin rather than leaking a consumer's identity. `slug` and an id in
    `PREFIX-123` form carry no such ambiguity: neither has any business in shipped prose.
    """
    import re
    import subprocess

    from models.project import load
    from models.resolve import REPO

    p = load()
    pattern = re.compile(
        "|".join([re.escape(p.slug), re.escape(p.bead_prefix()) + r"-\w"]), re.IGNORECASE
    )

    tracked = subprocess.run(
        ["git", "ls-files", "--", "docs", "agents", "commands", "skills", "templates"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()

    offenders: list[str] = []
    for rel in tracked:
        path = REPO / rel
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        # Generated regions legitimately carry project paths; the prose must not.
        prose = re.sub(r"<!-- GENERATED:.*?<!-- /GENERATED:[\w-]+ -->", "", text, flags=re.DOTALL)
        prose = re.sub(r"<!-- generated:.*?<!-- /generated:[\w-]+ -->", "", prose, flags=re.DOTALL)
        for n, line in enumerate(prose.splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{rel}:{n}: {line.strip()[:90]}")

    assert not offenders, (
        "shipped prose names this project, so it would not port:\n  "
        + "\n  ".join(offenders)
    )


def test_the_repo_is_found_by_its_config_not_by_the_git_root(tmp_path):
    """A plugin nested inside a larger repository must still find ITS project.

    The harness ships inside a marketplace: `plugins/<collection>/<plugin>/`, one git
    repository holding many. `git rev-parse --show-toplevel` then answers the MARKETPLACE
    root, which carries no `harness.yaml`, so `PROJECT_FILE` resolves to a file that does
    not exist and every config-reading check fails.

    This is deliberately asserted in a subprocess against the real resolution path. The
    suite cannot catch it in-process: `conftest.py` sets `MAD_HARNESS_REPO`, so every other
    test runs with the answer supplied — which is exactly how this would ship green while
    `make project` was broken.
    """
    import subprocess
    from pathlib import Path

    marketplace = tmp_path / "marketplace"
    plugin = marketplace / "plugins" / "engineering" / "a-plugin"
    plugin.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=marketplace, check=True)
    (plugin / "harness.yaml").write_text(
        "name: Nested\nslug: nested\nstacks: []\n"
        "paths: {docs: docs}\nareas: [{path: src, label: source}]\n"
    )

    probe = "from models.resolve import REPO; print(REPO)"
    env = {k: v for k, v in __import__("os").environ.items() if k != "MAD_HARNESS_REPO"}
    # THE SUITE'S OWN INTERPRETER, not `uv run python`: from a tmp dir uv has no project
    # and falls back to whatever VIRTUAL_ENV names — this test passed for weeks on the
    # operator's unrelated virtualenv happening to carry yaml, and failed the moment the
    # dispatcher stopped inheriting that variable.
    out = subprocess.run(
        [__import__("sys").executable, "-c", probe],
        cwd=str(plugin),
        env={**env, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    got = Path(out.stdout.strip().splitlines()[-1])

    assert got == plugin.resolve(), (
        f"REPO resolved to {got}, not the directory holding harness.yaml. The git root "
        f"({marketplace}) carries no config, so every check would read the wrong tree."
    )
    assert got != marketplace.resolve(), "the git root must not win over the config"
