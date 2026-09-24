"""Technology modules: the layering that lets support grow without growing cost.

THE INVARIANT THESE TESTS EXIST FOR. A preloaded skill is a tax paid on every
dispatch whether or not it is read. If each supported technology added preloaded
doctrine, six of them would add ~9,000 tokens to EVERY dispatch — and a Python task
would pay to carry Go, Rust and Ruby knowledge it never opens.

So: adding a module must not change any agent's preload bill. That is asserted here
rather than promised in a README.
"""

from __future__ import annotations

import textwrap

import pytest
import yaml

from models.context import active_modules, render_card
from models.project import FRAMEWORKS_DIR, ProjectError, load

# --- the cost guarantee -------------------------------------------------------


def _preload_totals() -> dict[str, int]:
    """Every agent's preloaded character count, as check-skills computes it."""
    import re

    from models.check_skills import AGENTS_DIR, skill_files

    available = skill_files()
    out: dict[str, int] = {}
    for path in sorted(AGENTS_DIR.glob("*.md")):
        head = path.read_text()[: path.read_text().index("\n---\n", 3)]
        declared = re.findall(r"^\s+- (\S+)$", head, re.M)
        out[path.stem] = sum(
            len(available[s].read_text()) for s in declared if s in available
        )
    return out


def test_adding_a_module_does_not_change_any_agent_preload_bill(tmp_path, monkeypatch):
    """THE POINT OF THE WHOLE DESIGN.

    A new technology must cost nothing to the dispatches that do not use it. If this
    ever fails, someone has put a module skill into an agent's `skills:` frontmatter
    and quietly made every project pay for one project's toolchain.
    """
    before = _preload_totals()

    import models.project as proj_mod

    (tmp_path / "brand-new-framework.yaml").write_text(
        textwrap.dedent("""
            name: brand-new-framework
            description: A framework the harness has never heard of.
            detect: [nothing.txt]
            card: |
              Brand new:
              - a rule
        """)
    )
    monkeypatch.setattr(proj_mod, "FRAMEWORKS_DIR", tmp_path)

    after = _preload_totals()
    assert after == before, (
        "adding a technology module changed an agent's preload bill — the cost of "
        "supporting a toolchain must be zero for projects that do not use it"
    )


def test_no_agent_preloads_a_technology_skill():
    """The one mistake that would silently reintroduce the tax."""
    import re

    from models.check_skills import AGENTS_DIR, MODULE_SKILL_PREFIXES

    for path in sorted(AGENTS_DIR.glob("*.md")):
        head = path.read_text()[: path.read_text().index("\n---\n", 3)]
        for s in re.findall(r"^\s+- (\S+)$", head, re.M):
            assert not s.startswith(MODULE_SKILL_PREFIXES), (
                f"{path.stem} preloads {s!r}. Technology doctrine loads on demand; "
                f"preloading charges every dispatch in every project."
            )


def test_every_card_is_within_its_budget():
    """A card is paid on every dispatch in its lane, so it cannot grow unbounded."""
    from models.check_skills import CARD_BUDGET_CHARS

    p = load()
    cards = [(s.name, s.card_raw) for s in p.stacks if s.card_raw]
    cards += [(f["name"], (f.get("card") or "").strip()) for f in p.framework_configs()]
    for name, card in cards:
        assert len(card) <= CARD_BUDGET_CHARS, (
            f"card {name!r} is {len(card)} chars; move depth into its doctrine skill"
        )


# --- the two axes -------------------------------------------------------------


def test_a_framework_is_loadable_and_separate_from_the_toolchain(tmp_path, monkeypatch):
    """Toolchain and framework are independent: one toolchain serves many
    frameworks, and one framework runs on many toolchains. A harness with a single
    axis cannot serve Python+FastAPI after being taught Python+Django."""
    import models.project as proj_mod

    (tmp_path / "fastapi.yaml").write_text(
        "name: fastapi\ndescription: x\ndetect: [main.py]\ncard: |\n  FastAPI:\n  - a rule\n"
    )
    monkeypatch.setattr(proj_mod, "FRAMEWORKS_DIR", tmp_path)

    p = load()
    object.__setattr__(p, "raw", {**p.raw, "frameworks": ["fastapi"]})
    assert p.framework_configs()[0]["name"] == "fastapi"


def test_an_unknown_framework_names_what_is_available():
    p = load()
    object.__setattr__(p, "raw", {**p.raw, "frameworks": ["cobol-cics"]})
    with pytest.raises(ProjectError, match="Available:"):
        p.framework_configs()


def test_the_shipped_framework_modules_all_parse():
    for f in FRAMEWORKS_DIR.glob("*.yaml"):
        d = yaml.safe_load(f.read_text()) or {}
        if f.stem.startswith("_"):
            continue
        assert d["name"] == f.stem, f"{f.name} declares a name that is not its filename"
        assert d.get("card"), f"{f.name} has no card"


# --- lane scoping -------------------------------------------------------------


def test_an_unmapped_lane_returns_everything_rather_than_nothing():
    """Erring toward a larger card is recoverable; silently withholding the one rule
    that would have prevented a mistake is not."""
    assert active_modules("a-lane-nobody-declared") == active_modules(None)


def test_a_project_with_no_cards_gets_no_prompt_fragment():
    """Empty is a legitimate answer — a header explaining there is nothing to say is
    pure cost."""
    from models.project import Project

    empty = Project(
        name="x", slug="x", stacks=(), paths={}, areas=(), security={}, raw={}
    )
    assert render_card("any", empty) == ""


def test_the_card_names_its_depth_skill_so_an_agent_can_go_deeper():
    p = load()
    if not any(s.card_raw for s in p.stacks):
        pytest.skip("this project declares no cards")
    text = render_card(None, p)
    for s in p.stacks:
        if s.card_raw and s.raw.get("doctrine_skill"):
            assert s.raw["doctrine_skill"] in text


def test_no_agent_names_a_technology():
    """The coupling this change removed must not grow back.

    An agent that names a toolchain or framework cannot serve a project using a
    different one — and the failure is not an error, it is confident wrong advice.
    Technology reaches an agent through its lane's card, never through its prose.
    """
    import re
    from pathlib import Path

    from models.check_skills import AGENTS_DIR
    from models.resolve import _prompts_dir

    # Widened after a review found factory_boy, mypy, Playwright, Tailwind, shadcn,
    # Zod, TanStack Query, .tsx, .objects. and httpOnly all sailing past the old
    # pattern — and found that scanning only agents/ missed test-doctrine, which names
    # more technology than any agent.
    # Widened twice. The first pass caught tool names; a second review found the list
    # was what let most of the remaining coupling through — bare LANGUAGE names, the
    # framework APIs, and one project's make targets all sailed past it. A guard whose
    # word list is narrower than the coupling reports clean and teaches nobody.
    banned = re.compile(
        # toolchains and runners
        r"\b(uv run|uv sync|npm |npx |yarn |pnpm |pytest|vitest|jest\b|playwright|"
        # languages, named directly
        r"python\b|typescript|javascript\b|golang\b|\brust\b|"
        # frameworks and their APIs
        r"django|next\.js|nextjs|react hook form|tanstack|tailwind|shadcn|zod|"
        r"on_delete|\.objects\.|select_related|prefetch_related|page\.route|"
        r"anonratethrottle|waitforurl|"
        # files and paths that name a toolchain
        r"tsconfig|node_modules|\.venv|\.tsx|manage\.py|package\.json|pyproject\.toml|"
        r"factory_boy|mypy|ruff|httponly|"
        # one project's aggregate targets
        r"make (typecheck|lint|test|e2e|reseed|seed|backend|frontend|worker))",
        re.IGNORECASE,
    )
    scanned: list[Path] = []
    for d in (AGENTS_DIR, _prompts_dir("commands"), _prompts_dir("skills")):
        scanned += sorted(d.rglob("*.md"))
    # Technology modules exist to name technologies; everything else must not.
    # harness-setup names toolchains because helping a user pick one IS its job; the
    # technology modules name them for the same reason.
    exempt = ("stack-", "framework-", "harness-setup")
    scanned = [p for p in scanned if not p.parent.name.startswith(exempt)]
    assert len(scanned) > 25, (
        f"scanned only {len(scanned)} prompt files — wrong directories"
    )

    offenders = [
        f"{p.parent.name}/{p.name}:{i}: {line.strip()[:70]}"
        for p in scanned
        for i, line in enumerate(p.read_text().splitlines(), 1)
        # A fenced-code language tag (```python) is markdown syntax naming the
        # snippet's language, not advice to the agent about its project.
        if banned.search(line) and not line.lstrip().startswith("```")
    ]
    assert not offenders, (
        f"{len(offenders)} prompt lines name a technology; it must reach an agent through "
        f"its lane's card, never its prose:\n  " + "\n  ".join(offenders[:25])
    )


# --- the split the conftest hides ---------------------------------------------


def test_the_harness_works_against_a_repo_it_does_not_live_in(tmp_path):
    """THE TEST WHOSE ABSENCE LET EVERY PLUGIN BUG THROUGH.

    `conftest.py` sets MAD_HARNESS_REPO to the plugin root, so every other test runs
    with REPO == PLUGIN_ROOT — exactly the in-tree layout the extraction existed to
    leave behind. The plugin-vs-repo split was therefore never exercised by anything,
    and five check scripts, two module paths and a bootstrap invocation all resolved
    against the wrong tree while the suite stayed green.

    This test points the harness at a throwaway repo and asserts the two roots come
    out different and each resolves to its own side.
    """
    import subprocess

    consumer = tmp_path / "consumer"
    (consumer / ".harness").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=consumer, check=True)
    (consumer / "harness.yaml").write_text(
        "name: Consumer\nslug: consumer\nstacks: []\nbeads:\n  prefix: CONS\n"
        "paths:\n  docs: docs\nareas:\n  - path: src\n    label: source\n"
    )

    probe = (
        "import json;from models.resolve import REPO,HARNESS,PLUGIN_ROOT,AGENTS_DIR;"
        "from models.project import load;"
        "print(json.dumps({'repo':str(REPO),'harness':str(HARNESS),"
        "'agents':str(AGENTS_DIR),'name':load().name}))"
    )
    out = subprocess.run(
        ["uv", "run", "python", "-c", probe],
        cwd=str(
            HARNESS_DIR := __import__("pathlib").Path(__file__).resolve().parents[1]
        ),
        env={**__import__("os").environ, "MAD_HARNESS_REPO": str(consumer)},
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    d = __import__("json").loads(out.stdout.strip().splitlines()[-1])

    assert d["repo"] == str(consumer), "REPO must be the consuming repository"
    assert d["harness"] == str(HARNESS_DIR), "HARNESS must stay where the code lives"
    assert d["repo"] != d["harness"], "the whole point: the two roots differ"
    assert d["agents"].startswith(d["harness"].rsplit("/", 1)[0]), (
        "agents ship WITH the plugin; resolving them under the consuming repo is the "
        "bug class that broke check-analyst-mirror and check_project"
    )
    assert d["name"] == "Consumer", "config must come from the consuming repo"


def test_no_wrapper_resolves_a_sibling_application_project():
    """`uv run --project ../backend` assumed the origin's monorepo layout.

    In a plugin that path is `<plugin>/../backend`, which does not exist, so every
    Python entry point — dispatch, the lens brief, scan, peek, the worker bootstrap
    and three checks — failed at its first line.
    """
    from models.resolve import HARNESS

    offenders = [
        f"{p.relative_to(HARNESS)}:{i}"
        for p in HARNESS.rglob("*.sh")
        for i, line in enumerate(p.read_text().splitlines(), 1)
        if "--project" in line and "backend" in line
    ]
    assert not offenders, f"wrappers resolve a sibling application project: {offenders}"


# --- stack detection, which had no test at all --------------------------------


def _fixture_repo(tmp_path, files: dict[str, str]):
    """A throwaway repo with the given files, and a harness.yaml naming stacks."""
    for rel, body in files.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
    return tmp_path


@pytest.fixture
def stack_in(monkeypatch):
    """Load a stack module as seen from a given repo root.

    `present()` resolves REPO from the module namespace at CALL time, so the patch has
    to outlive the load — an earlier version restored it in a `finally` and every
    detection assertion silently ran against the real repository instead.
    """
    import models.project as mod

    def _load(repo, entry):
        monkeypatch.setattr(mod, "REPO", repo)
        return mod._load_stack(entry)

    return _load


def test_a_root_layout_project_is_detected(tmp_path, stack_in):
    """The layout the harness could not see was the DEFAULT one — pyproject.toml and
    uv.lock at the repository root, which is the commonest Python shape there is."""
    repo = _fixture_repo(tmp_path, {"uv.lock": "", "pyproject.toml": ""})
    assert stack_in(repo, "python-uv").present()


def test_a_subdirectory_layout_is_detected_when_the_project_says_where(
    tmp_path, stack_in
):
    repo = _fixture_repo(
        tmp_path, {"backend/uv.lock": "", "backend/pyproject.toml": ""}
    )
    assert not stack_in(repo, "python-uv").present(), "root '.' must not see backend/"
    s = stack_in(repo, {"name": "python-uv", "root": "backend"})
    assert s.present()
    assert s.deps_path == "backend/.venv"


def test_a_poetry_project_does_not_pass_as_uv(tmp_path, stack_in):
    """THE DANGEROUS DIRECTION. A false negative fails loudly at config time; a false
    positive passes validation and fails later inside a worker's worktree, mid-wave,
    where the escalation policy reads it as the worker's fault rather than the
    config's."""
    repo = _fixture_repo(
        tmp_path, {"pyproject.toml": "[tool.poetry]\n", "poetry.lock": ""}
    )
    s = stack_in(repo, "python-uv")
    assert not s.present(), "pyproject.toml marks the LANGUAGE, not the toolchain"
    assert s.ambiguous(), "and it must say so, rather than reporting a bare absence"


def test_node_detection_needs_the_toolchain_marker_too(tmp_path, stack_in):
    repo = _fixture_repo(tmp_path, {"package.json": "{}"})
    s = stack_in(repo, "node-npm")
    assert not s.present() and s.ambiguous(), (
        "package.json is shared with pnpm/yarn/bun"
    )
    (tmp_path / "package-lock.json").write_text("{}")
    assert stack_in(repo, "node-npm").present()


def test_root_moves_every_derived_path_together(tmp_path, stack_in):
    """The four-field drift this replaces: detect, dependency_dir, bootstrap.cwd and
    commands.cwd each carried the prefix independently, with nothing checking they
    agreed."""
    repo = _fixture_repo(tmp_path, {"svc/uv.lock": ""})
    s = stack_in(repo, {"name": "python-uv", "root": "svc"})
    assert s.present()
    assert s.deps_path.startswith("svc/")
    assert s.bootstrap_cwd == "svc"
    assert s.commands_cwd == "svc"


def test_a_bare_string_stack_still_works(tmp_path, stack_in):
    """Rollback path: a project naming a stack the old way must keep working."""
    repo = _fixture_repo(tmp_path, {"uv.lock": ""})
    assert stack_in(repo, "python-uv").root == "."


def test_every_shipped_module_declares_a_toolchain_marker():
    """Without one, nothing can tell whether the stack is really in use."""
    import yaml

    from models.project import STACKS_DIR

    for f in STACKS_DIR.glob("*.yaml"):
        if f.stem.startswith("_"):
            continue
        d = yaml.safe_load(f.read_text())
        assert d.get("detect_any"), f"{f.name} declares no detect_any"
        assert d.get("root") is not None, f"{f.name} declares no root"


# --- the card is no longer lane-filtered by default ---------------------------


def test_the_card_defaults_to_every_declared_module():
    """Stack and lane are orthogonal. For execution there is no selection at all —
    bootstrap and worker_env both iterate every stack — so the card should not invent
    one. Narrowing is opt-in; the default errs toward telling the agent too much,
    because withholding the one rule that mattered is the expensive direction."""
    p = load()
    lanes = p.raw.get("lanes") or {}
    narrowing = [
        n
        for n, v in lanes.items()
        if (v or {}).get("stacks") or (v or {}).get("frameworks")
    ]
    everything = {m.name for m in active_modules(None, p)}
    for lane in lanes:
        got = {m.name for m in active_modules(lane, p)}
        if lane in narrowing:
            assert got <= everything
        else:
            assert got == everything, f"lane {lane!r} narrowed without declaring stacks"


def test_no_prompt_tells_an_agent_to_run_a_bare_harness_path():
    """A bare `harness/verify/brief.sh` resolves only when the harness lives INSIDE
    the repository being worked on — true in-tree, false for every installed plugin,
    where an agent following the instruction gets "no such file".

    `CLAUDE_PLUGIN_ROOT` is deliberately NOT the fix: it is expanded in plugin config
    files but is absent from an agent's Bash environment (measured), so a prompt using
    it would expand to `/harness/...` and fail SILENTLY — worse than the honest error
    it replaced. The dispatcher exports `HARNESS_ROOT` and states it in the prompt.
    """
    import re

    from models.check_skills import AGENTS_DIR
    from models.resolve import _prompts_dir

    bare = re.compile(
        r"(?<![\w/$])harness/(verify|models|checks|swarm|campaign)/\S+\.(sh|py|mjs)"
    )
    offenders = [
        f"{p.parent.name}/{p.name}:{i}: {line.strip()[:70]}"
        for d in (AGENTS_DIR, _prompts_dir("commands"), _prompts_dir("skills"))
        for p in sorted(d.rglob("*.md"))
        for i, line in enumerate(p.read_text().splitlines(), 1)
        if bare.search(line)
    ]
    assert not offenders, (
        "prompts name harness scripts by a repo-relative path; use $HARNESS_ROOT/...:\n  "
        + "\n  ".join(offenders[:20])
    )


def test_the_dispatcher_tells_a_worker_where_the_harness_is():
    """Both channels, because a worker may arrive either way: the environment for a
    boundary dispatch, and the prompt text for the Agent tool, which cannot be handed
    an environment."""
    from models.dispatch import build_env, with_context
    from models.resolve import HARNESS, resolve

    assert build_env(resolve("verifier"))["HARNESS_ROOT"] == str(HARNESS)
    assert "$HARNESS_ROOT" in with_context("task", None)


def test_every_command_that_runs_a_harness_script_is_permitted_to():
    """A command that invokes a script its `allowed-tools` does not cover stops on a
    permission prompt — and in a background dispatch that prompt surfaces in the
    ORCHESTRATOR's session, where nobody is watching. That is the recorded multi-hour
    stall, three times over, and this is the same shape re-armed by rewriting 79 paths
    to `$HARNESS_ROOT/...` without revisiting the rules that gate them.

    THE GRANT FORM ITSELF WAS THE BUG, and this test enforced it. Measured against the
    live CLI, in frontmatter as well as on the flag:

        Bash(${CLAUDE_PLUGIN_ROOT}/harness/*)   ALLOWED
        Bash($HARNESS_ROOT/:*)                  denied
        Bash(harness/:*)                        denied

    Two reasons, and each alone is fatal. The text before `:` is the COMMAND, and a
    DIRECTORY is not a command — `Bash(git:*)` works for exactly the reason
    `Bash(<dir>/:*)` does not. And a command naming a SHELL variable cannot be granted at
    all: matching is textual, so it matches neither the expanded path nor a rule spelled
    the same way.

    `${CLAUDE_PLUGIN_ROOT}` is different in kind: the PLUGIN LOADER expands it, in the
    frontmatter and the body alike, so both sides are absolute by the time anything is
    matched. `$HARNESS_ROOT` cannot serve here — it is exported by the dispatcher and is
    UNSET in an interactive session, where these commands actually run.
    """
    import re

    from models.resolve import _prompts_dir

    skills_dir = _prompts_dir("skills")

    def invokes(text: str, depth: int = 0) -> str | None:
        """Whether this prompt runs a harness script — directly, or through a skill.

        FOLLOWING THE INDIRECTION IS THE POINT. Both campaign commands are four-line
        shims saying "follow the campaign-loop skill"; the 23 invocations across 14
        scripts live in that skill and run under the SHIM's frontmatter. An earlier
        version of this test checked only files that themselves contained an
        invocation, so it missed exactly the two files that were exposed. The grant and
        the call are separated by one indirection, which is what hides it from a reader
        auditing the command file too.
        """
        if "$HARNESS_ROOT/" in text or "${CLAUDE_PLUGIN_ROOT}/harness/" in text:
            return "directly"
        if depth:
            return None
        for m in re.finditer(
            r"`([a-z][a-z-]+)` skill|skills/([a-z-]+)/SKILL\.md", text
        ):
            name = m.group(1) or m.group(2)
            f = skills_dir / name / "SKILL.md"
            if f.exists() and invokes(f.read_text(), depth + 1):
                return f"via the `{name}` skill"
        return None

    offenders = []
    for p in sorted(_prompts_dir("commands").glob("*.md")):
        text = p.read_text()
        head = text[: text.index("\n---\n", 3)]
        how = invokes(text)
        if how and "Bash(${CLAUDE_PLUGIN_ROOT}/harness/*)" not in head:
            offenders.append(
                f"{p.name}: runs harness scripts {how}, but does not permit it"
            )
    assert not offenders, "\n  ".join(offenders)


def test_the_prompts_contain_no_broken_prose():
    """THE CLASS THREE REVIEWS FOUND AND NO OTHER GUARD CAN SEE.

    Every finding in those reviews was grammatical text that parses, imports and tests
    clean while saying something broken: a sentence whose subject was deleted, a rule
    stated twice, a bullet ending on a comma. `make check` was green with fourteen of
    them present.

    They share one cause — an identifier removed without reading back the sentence left
    behind — so the durable fix is not another sweep but this: the rule made mechanical,
    because a rule nobody can forget beats a rule everybody is told to remember.
    """
    from models.check_prose import scan

    findings = scan()
    assert not findings, "broken prose:\n  " + "\n  ".join(
        f"{f.path.parent.name}/{f.path.name}:{f.line} [{f.rule}] {f.text[:70]}"
        for f in findings[:20]
    )


# --- fidelity is a lane-activated module, not a preloaded skill ----------------


def test_design_fidelity_is_not_preloaded_by_a_generalist():
    """It reaches a worker through its lane's card, like every other module axis.

    `design-fidelity` is ~890 tokens and was preloaded by `fullstack-engineer`, the
    most-dispatched agent in the harness — so every backend and docs dispatch, in every
    project including ones with no design handover, paid for it and was told to run a
    command it could not run. That is the exact tax the stack/framework layering exists
    to remove; fidelity was the axis that had not had the treatment.

    `fidelity-auditor` is exempt because fidelity IS its activity: every dispatch of it
    reads that doctrine, so preloading costs nothing wasted, and a project without a
    handover never dispatches it at all.
    """
    import re

    from models.check_skills import AGENTS_DIR

    offenders = []
    for path in sorted(AGENTS_DIR.glob("*.md")):
        if path.stem == "fidelity-auditor":
            continue
        head = path.read_text()[: path.read_text().index("\n---\n", 3)]
        if "design-fidelity" in re.findall(r"^\s+- (\S+)$", head, re.M):
            offenders.append(path.stem)
    assert not offenders, (
        f"{offenders} preload `design-fidelity`. It is lane-activated — declare "
        f"`fidelity: true` on the lanes that do that work instead."
    )


def test_fidelity_activates_only_on_a_lane_that_asks_for_it():
    """Opt-in, unlike stacks and frameworks. A stack defaults to active because a
    worker may touch anything; fidelity is a distinct activity a lane either does or
    does not do, and a project with no handover must never see it."""
    from models.context import active_modules
    from models.project import Project

    fid = {"card": "Design fidelity:\n- a rule", "doctrine_skill": "design-fidelity"}
    p = Project(
        name="x",
        slug="x",
        stacks=(),
        paths={},
        areas=(),
        security={},
        raw={
            "fidelity": fid,
            "lanes": {
                "backend": {"stacks": ["s"]},
                "ui": {"fidelity": True, "stacks": ["s"]},
            },
        },
    )
    assert "fidelity" in [m.name for m in active_modules("ui", p)], (
        "a fidelity lane that also declares stacks must keep the card — the narrowing "
        "matches stack and framework names only, so appending before it filtered it out"
    )
    assert "fidelity" not in [m.name for m in active_modules("backend", p)]

    without = Project(
        name="x", slug="x", stacks=(), paths={}, areas=(), security={}, raw={}
    )
    assert not [m for m in active_modules("ui", without) if m.name == "fidelity"]


# --- the harness's own conventions, repatriated from CLAUDE.md ----------------

CONVENTION_CARRIERS = ("evidence-gathering", "spec-lifecycle")


def test_every_agent_reaches_the_conventions_through_a_skill_it_preloads():
    """THE COVERAGE ARGUMENT, asserted rather than reasoned.

    The gloss and cite-by-symbol rules were restated in nine agents, each citing
    `CLAUDE.md` — a project file the harness does not generate, cannot validate, and had
    no business depending on for rules it defines and enforces itself. Both cited
    sections sat under that file's `## Tasks Issue Tracker` heading: they are about
    tasks, which is the harness's tracker.

    They now live in two skills, chosen because between them every agent that writes a
    task or a report preloads at least one (three, until the writers took
    `evidence-gathering` and `worker-protocol`'s copy became a second charge on every
    writer dispatch). If an agent stops preloading its only carrier, the rules silently
    stop reaching it — so the coverage is checked, not assumed, here and in the check.
    """
    import re

    from models.check_skills import AGENTS_DIR, skill_files

    available = skill_files()
    carriers = {
        n
        for n in CONVENTION_CARRIERS
        if n in available and "HARNESS CONVENTIONS" in available[n].read_text()
    }
    assert carriers == set(CONVENTION_CARRIERS), (
        f"missing carriers: {set(CONVENTION_CARRIERS) - carriers}"
    )

    uncovered = []
    for path in sorted(AGENTS_DIR.glob("*.md")):
        text = path.read_text()
        head = text[: text.index("\n---\n", 3)]
        declared = set(re.findall(r"^\s+- (\S+)$", head, re.M))
        if declared and not (declared & carriers):
            uncovered.append(f"{path.stem} preloads {sorted(declared)}")
    assert not uncovered, (
        "these agents preload no skill carrying the harness conventions:\n  "
        + "\n  ".join(uncovered)
    )


def test_the_check_fails_an_agent_that_preloads_no_carrier(monkeypatch, tmp_path):
    """The coverage argument, as the check's own failure — not only the suite's."""
    from models import check_conventions as mod

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "loner.md").write_text("---\nname: loner\nskills:\n  - test-doctrine\n---\nbody\n")
    (agents / "bare.md").write_text("---\nname: bare\n---\nno preloads at all is fine\n")
    real = mod._prompts_dir
    monkeypatch.setattr(mod, "_prompts_dir", lambda kind: agents if kind == "agents" else real(kind))
    assert mod._uncovered(set(mod.CARRIERS)) == ["loner preloads ['test-doctrine']"]
    assert mod.main() == 1


def test_the_conventions_block_is_identical_across_its_carriers():
    """Three statements of one rule is the shape that produced most of what four
    reviews found. Duplication is only safe when divergence is mechanical."""
    from models.check_conventions import CARRIERS, extract
    from models.resolve import _prompts_dir

    skills = _prompts_dir("skills")
    blocks = {n: extract((skills / n / "SKILL.md").read_text()) for n in CARRIERS}
    assert all(blocks.values()), (
        f"missing block: {[k for k, v in blocks.items() if not v]}"
    )
    assert len(set(blocks.values())) == 1, "the conventions block has diverged"


def test_the_harness_no_longer_cites_claude_md_for_its_own_rules():
    """From 26 citations to the handful that are genuinely about a project's own
    conventions file. Asserted so it cannot creep back one restatement at a time."""
    import re

    from models.check_skills import AGENTS_DIR
    from models.resolve import _prompts_dir

    hits = [
        f"{p.parent.name}/{p.name}:{i}"
        for d in (AGENTS_DIR, _prompts_dir("commands"), _prompts_dir("skills"))
        for p in sorted(d.rglob("*.md"))
        for i, line in enumerate(p.read_text().splitlines(), 1)
        if re.search(r"CLAUDE\.md", line)
    ]
    assert len(hits) <= 3, (
        f"{len(hits)} citations of CLAUDE.md; the harness defines and enforces these "
        f"rules itself:\n  " + "\n  ".join(hits)
    )


# --- each prose rule, proven to fire ------------------------------------------


def test_each_prose_rule_catches_the_shape_it_is_named_for():
    """PROVEN NECESSARY BY MUTATION. Disabling the repeated-sentence rule with
    `if False:` killed no test — `scan()` returns nothing on a clean tree whether the
    rule works or not, so asserting cleanliness asserts nothing about the rules.

    That is the decorative test `test-doctrine` §3 exists to catch, in the guard built
    to catch a class no other guard sees. Each rule now has a positive case.
    """
    from pathlib import Path

    from models.check_prose import check_text

    cases = {
        "repeated-sentence": "The rule applies. Docs only. Docs only.",
        "double-space": "- A task derived   when one user may see another's data",
        "dangling-comma": None,  # needs a following blank line; covered below
        "orphan-fragment": "The sentence ended here.\nlowercase continuation of nothing",
        "repeated-across-wrap": "including empty and error\nerror — with the longest names",
        "repeated-word": "declared `security.invariants` it invariants its change touches",
    }
    for rule, text in cases.items():
        if text is None:
            continue
        rules = {f.rule for f in check_text(Path("x.md"), text)}
        assert rule in rules, (
            f"rule {rule!r} did not fire on its own shape: {rules or 'nothing'}"
        )

    # The dangling comma needs the break that makes it a defect.
    rules = {
        f.rule
        for f in check_text(Path("x.md"), "a sentence that ends on a comma,\n\nnext")
    }
    assert "dangling-comma" in rules


def test_no_prose_rule_fires_on_well_formed_prose():
    """The other half. A linter that cries wolf gets disabled, and then catches nothing
    — so every construction that produced a false positive while tuning it is pinned
    here, and each is real English this corpus actually uses."""
    from pathlib import Path

    from models.check_prose import check_text

    fine = [
        "the decorative test `test-doctrine` §3 exists to catch",  # prefix at a boundary
        "Compare side by side at 390px and 1280px.",  # idiom
        "mark the lanes that do that work with `fidelity: true`",  # function word
        "- **trap.** `    x += f(a)` at 12 spaces is a substring",  # code span
        "-   an aligned list item",  # bullet alignment
        "Name every doc it lands in** in the frontmatter's list",  # awkward, not broken
    ]
    for text in fine:
        found = check_text(Path("x.md"), text)
        assert not found, f"false positive on {text[:44]!r}: {[f.rule for f in found]}"


def test_the_prose_scan_actually_reads_files():
    """A scope that matches nothing reports clean. An earlier pass 'widened' the scan to
    `templates/`, where every file is `.yaml` — so zero matched and the widening was
    inert, which is the empty-reads-as-clean failure this repo warns about, committed
    inside the fix for it."""
    from models.check_prose import _scanned

    n = _scanned()
    assert n > 30, (
        f"the prose scan sees only {n} files — its scope has stopped matching"
    )

    # `docs/` IS IN SCOPE. It is the largest body of prose here and the one people read end
    # to end, and it was outside the scan until 0.10.35 — the same empty-reads-as-clean
    # shape as the `templates/` widening above, one directory over.
    from models.check_prose import scan
    from models.resolve import PLUGIN_ROOT

    docs = PLUGIN_ROOT / "docs"
    assert docs.is_dir()
    pages = {p for p in docs.rglob("*.md")}
    assert len(pages) > 20, "the documentation corpus, or the glob that finds it, has moved"
    # A planted defect in a docs page must be reported, or the scope is decorative.
    broken = docs / "_scope_probe.md"
    broken.write_text("A sentence that ends.\nand a lowercase continuation after it.\n")
    try:
        hits = [f for f in scan() if f.path == broken]
    finally:
        broken.unlink()
    assert hits and hits[0].rule == "orphan-fragment", "a docs page is scanned like any other prose"


def test_the_domain_report_runs_and_scans_the_harness():
    """The domain check is a REPORT, not a gate, and this asserts only that it works.

    It was a blocking test. The numbers killed that: 133 derived terms produced 94 hits,
    every one false — `system`, `health`, `messages`, `analytics`, words a product uses
    for its features and a harness uses for its own machinery. Suppressing them took a
    hand-written exclusion list that grows every time the product adds a
    generic-sounding feature, so the maintenance never ends and the failure mode is a CI
    break on text that was always fine.

    A domain word in harness prose is not automatically wrong: "check the message board"
    is a leak, "the orchestrator's session receives the message" is not. Only a reader
    can tell them apart, which makes this a thing to look at rather than a thing to
    fail. What stays a gate is the project's NAME, SLUG and BEAD PREFIX — unambiguous,
    and blocked by `test_no_project_identifier_leaks_into_the_shipped_corpus`.

    So what is pinned here is that the report reads the right tree and is not silently
    scanning nothing.
    """
    from models.domain_report import harness_files
    from models.resolve import PLUGIN_ROOT

    files = harness_files()
    assert len(files) > 100, f"the report sees only {len(files)} harness files"
    assert all(PLUGIN_ROOT in f.parents or f.parent == PLUGIN_ROOT for f in files), (
        "the report must scan the PLUGIN, never the consuming repository — an earlier "
        "version walked the consuming repo and reported 5,606 hits, every one a product "
        "legitimately using its own words"
    )


def test_no_shipped_instruction_tells_a_worker_to_source_swarm_env():
    """A command the permission system refuses must not be handed to an agent to run.

    `source .swarm-env` was instructed in seven places — two stack cards, the first-run
    hint, three agents and three skills — while `models/commands.py` documented that the
    form CANNOT BE PERMITTED and that `run_key` exists precisely so a worker never writes
    it. It cost a real denial in every wave. It could not have worked regardless: each
    Bash call is a fresh shell, so the exports never reach the next command.

    SCOPED TO COPYABLE INSTRUCTIONS — lines inside a fenced code block — because the
    prose that documents the trap necessarily contains the string, and a sweep that
    matched everything would flag its own explanation. That has happened three times in
    this corpus already.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent.parent
    offenders = []
    for md in (
        sorted(root.glob("agents/*.md"))
        + sorted(root.glob("skills/*/SKILL.md"))
        + sorted(root.glob("commands/*.md"))
    ):
        in_fence = False
        for n, line in enumerate(md.read_text().splitlines(), 1):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence and re.match(r"\s*source\s+\.swarm-env", line):
                offenders.append(f"{md.relative_to(root)}:{n}")
    assert not offenders, (
        "these hand a worker a command that is always denied; run tests through "
        f"harness/verify/run.sh, which loads .swarm-env itself: {offenders}"
    )


def test_the_generated_documentation_matches_its_sources():
    """The docs' enumerable parts are data, and hand-maintained mirrors of data drift.

    The drift is invisible in the worst way: the page still reads correctly, it is simply
    wrong. Writing these pages a tier was documented as `max` when the tier is called
    `strategic`, and a script was cited that does not exist — both caught only because
    someone chose to look. This makes looking unnecessary.
    """
    from models.check_docs import main

    assert main([]) == 0, "run harness/checks/check-docs.sh --write"


def test_the_drift_check_can_actually_fail(tmp_path, monkeypatch):
    """A guard that cannot fail is decoration. This corpus has shipped three."""
    import models.check_docs as D

    target = tmp_path / "agents.md"
    start = D.START.format(key="agents")
    target.write_text(f"# x\n\n{start}\n\nSTALE\n\n{D.END.format(key='agents')}\n")
    monkeypatch.setattr(D, "TARGETS", {"agents": target})
    monkeypatch.setattr(D, "DOCS", tmp_path)

    assert D.main([]) == 1, "stale generated content must fail"
    assert D.main(["--write"]) == 0
    assert "STALE" not in target.read_text()
    assert D.main([]) == 0, "after a rewrite it must be clean"


def test_the_retrieval_index_names_every_documentation_page():
    """`llms.txt` exists so an agent reads ONE small file and then ONE page, instead of
    guessing among twenty or reading them all. A page missing from it is a page an agent
    cannot find."""
    from pathlib import Path

    from models.check_docs import DOCS

    index = (DOCS / "llms.txt").read_text()
    for page in DOCS.glob("**/*.md"):
        rel = page.relative_to(DOCS)
        if rel == Path("README.md"):
            continue
        assert f"docs/{rel}" in index, f"{rel} is absent from llms.txt"


def test_the_credential_file_is_gitignored_and_its_template_is_not():
    """`.env.example` says "NEVER commit harness/.env" and `resolve.py` loads that file
    automatically — a rule with no mechanism until 2026-09-23, when a provider key was
    about to be written into a repository whose .gitignore had no rule for it. The
    template stays tracked; the credential cannot be."""
    import subprocess

    from models.resolve import HARNESS

    def ignored(path):
        return subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=str(HARNESS), capture_output=True).returncode == 0

    assert ignored(HARNESS / ".env"), "harness/.env is not ignored — a provider key would be committable"
    assert not ignored(HARNESS / ".env.example"), "the template must stay tracked"
    assert (HARNESS / ".env.example").is_file()


def test_the_probe_passes_tools_as_one_comma_separated_value(monkeypatch, tmp_path):
    """`--tools <tools...>` is variadic and takes "Bash,Edit,Read" (or "" for none). Spelled
    as separate words it swallowed the prompt as another tool name, the CLI exited "Input
    must be provided", and the probe blamed the PROVIDER — which is why its tool-call and
    multi-turn probes had never once run. Measured against DeepSeek, answering by hand at
    the same moment."""
    import subprocess

    from models import probe_compat

    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, '{"type":"result","result":"PONG","usage":{}}', "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    for tools, expected in (([], "--tools="), (["Read"], "--tools=Read"), (["Read", "Write", "Bash"], "--tools=Read,Write,Bash")):
        probe_compat._run({}, "m", "the prompt", tmp_path, tools=tools)
        cmd = seen["cmd"]
        assert cmd[-1] == "the prompt", f"the prompt must stay the last argument (tools={tools!r})"
        assert expected in cmd, f"attached, one comma-separated value (tools={tools!r})"
        assert "--tools" not in cmd, "detached spelling makes the variadic flag eat the prompt"
    probe_compat._run({}, "m", "the prompt", tmp_path, tools=None)
    assert not [a for a in seen["cmd"] if a.startswith("--tools")], "None means the CLI's default tool set"


def test_mutate_loads_the_worktrees_swarm_env_itself(tmp_path):
    """The isolation check demands the per-worker variables and used to say "Source
    .swarm-env first" — the one thing a worker cannot legally do (`source x && y` is a
    compound command; `VAR=x cmd` begins with an assignment; both match no permission rule
    and are denied). Measured in a lab wave: a worker followed test-doctrine to mutate.sh,
    was denied both spellings, and its dispatch was marked not-ok for work the harness had
    made unreachable. `run.sh` loads the file for the worker; so must this."""
    import subprocess

    from models.resolve import HARNESS

    text = (HARNESS / "verify" / "mutate.sh").read_text()
    assert '. "$PWD/.swarm-env"' in text and "set -a" in text, "the script sources it itself"
    told = [ln for ln in text.splitlines() if "Source .swarm-env first" in ln and not ln.lstrip().startswith("#")]
    assert not told, f"never instruct a worker to do the denied thing: {told}"

    # It really loads it: a worktree whose file sets a variable, read back by the script.
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".swarm-env").write_text("export PROBE_FROM_SWARM_ENV=loaded\n")
    script = wt / "probe.sh"
    head = text.split("TASK=")[0]
    script.write_text(head + 'echo "PROBE=${PROBE_FROM_SWARM_ENV:-unset}"\n')
    out = subprocess.run(["bash", str(script)], cwd=str(wt), capture_output=True, text=True, timeout=60)
    assert "PROBE=loaded" in out.stdout, out.stdout + out.stderr


def test_the_probe_reads_the_streamed_usage_and_warns_without_refusing_the_provider(monkeypatch, tmp_path, capsys):
    """A per-dispatch ceiling is enforced from the usage on each message AS IT ARRIVES,
    which is a different payload from the final one: a provider can report exact totals at
    the end and nothing on the way. That costs the CEILING, not the cost record and not
    the work — so it is reported, loudly, and does not refuse the provider. An operator who
    is not told believes in a ceiling they do not have."""
    import subprocess

    from models import probe_compat

    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        lines = [
            '{"type":"assistant","message":{"usage":{"input_tokens":35334,"cache_read_input_tokens":0,"output_tokens":0}}}',
            '{"type":"assistant","message":{"usage":{"input_tokens":35334,"cache_read_input_tokens":0,"output_tokens":0}}}',
            '{"type":"result","result":"PONG","usage":{"input_tokens":35334,"output_tokens":3}}',
        ]
        return subprocess.CompletedProcess(cmd, 0, "\n".join(lines), "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = probe_compat._run({}, "m", "p", tmp_path, tools=[], stream=True)
    assert "stream-json" in seen["cmd"] and "--verbose" in seen["cmd"], "-p stream-json needs --verbose"
    assert len(out["_streamed_usage"]) == 2, "every assistant message's usage, repeats included"
    assert out["result"] == "PONG", "the final result is still what is returned"

    # A provider that streams nothing: reported as WARN, and the run is still a pass.
    quiet = probe_compat.Probe("streamed token accounting", "why", False, "none of them", fatal=False)
    fatal = probe_compat.Probe("multi-turn tool loop", "why", False, "broken")
    monkeypatch.setattr(probe_compat, "probe", lambda p: [quiet])
    monkeypatch.setattr(probe_compat, "load_config", lambda: {"providers": {"deepseek": {}}, "tiers": {}})
    assert probe_compat.main(["deepseek"]) == 0, "an advisory miss never gates the provider"
    o, e = capsys.readouterr()
    assert "[WARN" in o and "WARN — deepseek does not provide: streamed token accounting" in e
    assert "0 required probes" not in o or "advisory" in o

    monkeypatch.setattr(probe_compat, "probe", lambda p: [quiet, fatal])
    assert probe_compat.main(["deepseek"]) == 1, "a fatal probe still refuses it"


def test_every_generated_table_has_a_page_to_write_itself_into():
    """MEASURED while adding the `modules` table (2026-09-24): the generator was written,
    the `GENERATED:modules` block was added to the page, `check-docs.sh --write` ran — and
    the block stayed EMPTY while the check reported OK, because nothing mapped the key to a
    file. A generator with no target is never called, and the page keeps an empty block that
    reads as correct: the empty-reads-as-clean failure this repo keeps re-finding."""
    from models.check_docs import GENERATORS, TARGETS

    assert set(GENERATORS) == set(TARGETS), (
        f"a generated table with no page, or a page with no generator: "
        f"{sorted(set(GENERATORS) ^ set(TARGETS))}"
    )
    for key, target in TARGETS.items():
        assert target.is_file(), f"{key} names a page that does not exist: {target}"
        body = GENERATORS[key]()
        assert body.count("\n") >= 2, f"the {key} table rendered no rows"
        assert f"GENERATED:{key}" in target.read_text(), f"{target.name} carries no {key} block"


def test_the_shipped_module_list_is_the_real_one():
    """A reader decides whether to adopt on this list, so it is generated rather than
    written: it was a link to a directory, which put 'which toolchains are supported' one
    click away from the page that claims to answer it."""
    from models.check_docs import modules_table
    from models.resolve import PLUGIN_ROOT

    table = modules_table()
    on_disk = {
        f.stem
        for axis in ("stacks", "frameworks")
        for f in (PLUGIN_ROOT / "harness" / axis).glob("*.yaml")
        if not f.stem.startswith("_") and not f.stem.endswith("-selftest")
    }
    assert on_disk, "no modules found — the glob has stopped matching"
    for name in on_disk:
        assert f"`{name}`" in table, f"{name} ships but is not in the table"
    # The template and this repo's own fixture are not products and must not be listed.
    assert "_template" not in table and "selftest" not in table
