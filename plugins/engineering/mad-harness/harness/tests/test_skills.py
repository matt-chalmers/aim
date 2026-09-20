"""The skill layer: declarations that resolve, and a preload bill somebody decided.

A preloaded skill is a tax paid on *every dispatch of every agent that declares
it*. The failure this guards against is silent: a skill grows, or a large one gets
preloaded somewhere hot, and every dispatch quietly gets dearer with nobody
choosing that.
"""

from __future__ import annotations

import re

import pytest

from models.check_skills import BUDGET_CHARS, SKILLS_DIR, skill_files
from models.resolve import AGENTS_DIR, agent_frontmatter

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def declared_skills(agent: str) -> list[str]:
    text = (AGENTS_DIR / f"{agent}.md").read_text()
    return re.findall(r"^\s+- (\S+)$", text[: text.index("\n---\n", 3)], re.M)


def agents_with_skills() -> list[str]:
    return [p.stem for p in sorted(AGENTS_DIR.glob("*.md")) if declared_skills(p.stem)]


def test_every_declared_skill_exists():
    available = skill_files()
    for agent in agents_with_skills():
        for s in declared_skills(agent):
            assert s in available, (
                f"{agent} declares skill {s!r} but there is no {SKILLS_DIR}/{s}/SKILL.md"
            )


def test_a_skill_declaration_without_the_skill_tool_is_a_dead_letter():
    """The silent one: frontmatter looks right, the agent never gets the doctrine,
    and it behaves exactly like one that was never given it."""
    for agent in agents_with_skills():
        tools = [
            t.strip()
            for t in str(agent_frontmatter(agent).get("tools") or "").split(",")
        ]
        assert "Skill" in tools, (
            f"{agent} declares {declared_skills(agent)} but has no `Skill` tool"
        )


def test_every_skill_name_matches_its_directory():
    """The directory is what a frontmatter reference resolves against, so a
    disagreement means one of the two is lying to whoever reads it next."""
    for name, path in skill_files().items():
        m = FRONTMATTER.match(path.read_text())
        assert m, f"skill {name!r} has no frontmatter"
        declared = re.search(r"^name:\s*(\S+)", m.group(1), re.M)
        assert declared and declared.group(1) == name, (
            f"skill dir {name!r} declares name={declared.group(1) if declared else None!r}"
        )


def test_every_skill_says_when_it_applies():
    """A skill with no description gives the reader nothing to decide relevance on."""
    for name, path in skill_files().items():
        m = FRONTMATTER.match(path.read_text())
        assert re.search(r"^description:\s*\S", m.group(1), re.M), (
            f"skill {name!r} has no description"
        )


def test_no_agent_exceeds_the_preload_budget():
    available = skill_files()
    for agent in agents_with_skills():
        total = sum(
            len(available[s].read_text())
            for s in declared_skills(agent)
            if s in available
        )
        assert total <= BUDGET_CHARS, (
            f"{agent} preloads {total:,} chars (~{total // 4:,} tokens), budget "
            f"{BUDGET_CHARS:,} — paid on every dispatch"
        )


def test_the_budget_is_calibrated_to_catch_the_real_failure():
    """A threshold that fires on the correct setup teaches people to ignore it.

    test-doctrine (~18k) is legitimately combined with focused skills; preloading
    campaign-loop (~56k) into a per-task agent is the mistake. The budget must sit
    between those two, or it is either noise or nothing.
    """
    available = skill_files()
    doctrine = len(available["test-doctrine"].read_text())
    loop = len(available["campaign-loop"].read_text())
    assert doctrine < BUDGET_CHARS < loop, (
        f"budget {BUDGET_CHARS:,} does not discriminate: test-doctrine={doctrine:,}, "
        f"campaign-loop={loop:,}"
    )


def test_the_writers_preload_evidence_gathering():
    """0.10.5: measured on the same epic, five runs per arm — cost per run -24% with the
    spreads apart, output tokens -36%. The preload is a per-dispatch charge that bought
    fewer, larger tool calls. It is why the budget above sits at 36,000, not 32,000."""
    for agent in ("fullstack-engineer", "quality-engineer"):
        assert "evidence-gathering" in declared_skills(agent), (
            f"{agent} no longer preloads evidence-gathering — the measured -24% goes with it"
        )


def test_the_four_lenses_all_carry_the_gate_doctrine():
    """The gate's rules — especially that L3 must not see the diff — are shared by
    all four lenses. Two of them preloaded nothing at all before this skill existed,
    which left the rule dependent on the orchestrator remembering to say it."""
    for lens in ("verifier", "verifier-tests", "verifier-spec", "verifier-security"):
        assert "verification-gate" in declared_skills(lens)
        assert "evidence-gathering" in declared_skills(lens)


def test_verifier_spec_is_told_it_must_not_read_the_diff_and_is_denied_it():
    """L3's independence is the whole reason its findings are worth having next to
    L1's. The skill states the rule; since 0.10.21 the dispatch ENFORCES it — the lens
    declares `evidence: no-diff` and is denied the diff root — and the skill says so,
    because a lens told "must not" without knowing it is also "cannot" may spend calls
    trying."""
    gate = skill_files()["verification-gate"].read_text()
    assert "must never see the diff" in gate
    assert "evidence: no-diff" in gate and "denied" in gate
    assert "diff/by-file/" not in gate, "the skill no longer names a path the lens is denied — naming it invites the attempt"


def test_the_isolation_invariant_reaches_workers_through_their_stack():
    """The per-worker isolation rule used to be repeated in every writer agent. It
    now lives with the toolchain that owns it — in the card a worker is given, and
    in the depth skill behind it — so it stays true for stacks that spell it
    differently and absent for stacks that do not need it at all."""
    from models.context import render_card
    from models.project import load

    p = load()
    if not any(s.card_raw for s in p.stacks):
        pytest.skip("this project declares no stack cards")
    card = render_card(None, p)
    assert card.strip(), "a project with stack cards must render a non-empty card"


def test_every_agent_carries_at_least_one_skill():
    """The collection is meant to be complete: every agent has an activity, and
    every activity has doctrine. An agent preloading nothing is either a gap in
    the skill set or an agent whose method lives only in its own prompt — both
    worth noticing deliberately rather than discovering later."""
    bare = [
        p.stem for p in sorted(AGENTS_DIR.glob("*.md")) if not declared_skills(p.stem)
    ]
    assert not bare, f"agents with no doctrine preloaded: {bare}"


def test_each_skill_is_actually_preloaded_by_someone():
    """A skill nobody loads is documentation filed in the wrong place — it belongs
    under docs/ where a human will find it, not in a channel built for agents."""
    used = {s for p in AGENTS_DIR.glob("*.md") for s in declared_skills(p.stem)}
    # Invoked rather than preloaded: campaign-loop by the campaign commands,
    # harness-setup by a human standing up the harness in a new repository.
    invoked = {"campaign-loop", "harness-setup"}
    # Technology doctrine is loaded on demand — preloading it would charge every
    # dispatch in every project for one project's toolchain.
    invoked |= {s for s in skill_files() if s.startswith(("stack-", "framework-"))}
    orphans = set(skill_files()) - used - invoked
    assert not orphans, f"skills nobody preloads or invokes: {sorted(orphans)}"


def test_the_fidelity_evidence_gate_is_reachable_from_the_work_that_needs_it():
    """The owner gates UI work on source-excerpt + re-run composite. The gate must stay
    reachable — but HOW it reaches a worker changed, and this test changed with it.

    It used to be preloaded by `fullstack-engineer`, which charged every backend and
    docs dispatch ~890 tokens for doctrine it would never read, in every project
    including ones with no design handover. Fidelity is now a lane-activated module: the
    card reaches a worker whose lane declares `fidelity: true`, and names the skill for
    depth. `fidelity-auditor` still preloads it, because fidelity IS its activity.

    The gate itself is unchanged; only its delivery is.
    """
    fid = skill_files()["design-fidelity"].read_text()
    assert "source excerpt" in fid and "composite" in fid
    assert "design-fidelity" in declared_skills("fidelity-auditor")

    # And a lane that declares fidelity is pointed at that same skill.
    from models.context import active_modules
    from models.project import Project

    p = Project(
        name="x",
        slug="x",
        stacks=(),
        paths={},
        areas=(),
        security={},
        raw={
            "fidelity": {
                "card": "Design fidelity:\n- a rule",
                "doctrine_skill": "design-fidelity",
            },
            "lanes": {"ui": {"fidelity": True}},
        },
    )
    mods = [m for m in active_modules("ui", p) if m.name == "fidelity"]
    assert mods and mods[0].doctrine_skill == "design-fidelity"


def test_spec_leads_code_is_stated_where_the_spec_agents_will_read_it():
    """The rule most easily violated by a well-meaning agent: closing a spec/code
    gap by deleting the spec instead of building toward it."""
    lifecycle = skill_files()["spec-lifecycle"].read_text()
    assert "never by deleting the spec" in lifecycle
    for agent in ("analyst", "analyst-survey", "spec-editor", "architect"):
        assert "spec-lifecycle" in declared_skills(agent)


# --- generated regions --------------------------------------------------------


def test_test_doctrine_carries_no_project_identifier():
    """It measured 95% generic and is now portable; the remaining 5% is generated."""
    from models.check_skills import skill_files
    from models.project import load

    p = load()
    text = skill_files()["test-doctrine"].read_text()
    # The generated regions legitimately contain project paths; the PROSE must not
    # name the project itself.
    import re

    prose = re.sub(
        r"<!-- generated:.*?<!-- /generated:[\w-]+ -->", "", text, flags=re.DOTALL
    )
    for ident in (p.name, p.slug, p.bead_prefix()):
        assert ident.lower() not in prose.lower(), (
            f"test-doctrine prose names {ident!r}; it should be generic or generated"
        )
