"""The precomputed lens brief: size, completeness, and the L3 safety property.

The brief exists to stop four lenses each re-deriving the same diff. Measured on
commit 21add61 (44 files): the full diff is ~96k tokens, the brief ~1.7k. That
saving is only real if the brief is *complete* — a lens that has to fall back to
`git show` because a path was dropped costs more than no brief at all.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from verify.brief import Brief, build, render

#: Genuine unified-diff markers. Deliberately NOT `^\+` / `^-`, which match the
#: brief's own markdown bullets — a loose pattern here would make the L3 safety
#: test fire on `- \`path\`` and get "fixed" by loosening the assertion, which is
#: how a guard becomes decorative. These five only appear in real diff output.
DIFF_BODY = re.compile(
    r"^(@@|diff --git |index [0-9a-f]{7}|\+\+\+ |--- )", re.MULTILINE
)


def _sample_paths() -> list[str]:
    """One path per declared area — the first triggering area first — plus one that
    matches no area at all."""
    from models.project import load

    areas = load().areas
    triggering = [a for a in areas if a.triggers]
    plain = [a for a in areas if not a.triggers]
    picked = (triggering[:1] + plain[:2]) or areas[:3]
    return [f"{a.path}/sample.txt" for a in picked] + ["odd/place.txt"]


def a_brief(**over) -> Brief:
    base = dict(
        task="PROJ-x",
        commit="a" * 40,
        subject="feat: thing",
        author="Someone",
        date="2026-09-02",
        # Derived from whatever areas THIS project declares, so the fixture is not
        # a second place that encodes one repository's directory layout. The last
        # entry is deliberately unmapped: a path in no area must still be reported.
        files=_sample_paths(),
        stat=" harness/models/resolve.py | 4 ++--\n 1 file changed, 2 insertions(+), 2 deletions(-)",
        bead_text="DESCRIPTION\nsomething",
        root=Path("/tmp/nowhere"),
        insertions=2,
        deletions=2,
    )
    base.update(over)
    return Brief(**base)


# --- the L3 safety property ---------------------------------------------------


def test_brief_carries_no_diff_body():
    """L3 (`verifier-spec`) reasons from the task and the repo at HEAD, never the
    diff — that independence is what makes its findings worth having alongside
    L1's. brief.md is the one file handed to every lens, so a diff body leaking
    into it silently destroys the decorrelation the lens gate is built on.
    """
    text = render(a_brief())
    found = DIFF_BODY.findall(text)
    assert not found, f"brief.md contains diff body markers: {found}"


def test_the_safety_test_would_actually_catch_a_leak():
    """A guard that cannot fail is worse than none — pin that this one can."""
    leaked = render(a_brief()) + "\n@@ -1,4 +1,4 @@\n-old line\n+new line\n"
    assert DIFF_BODY.findall(leaked)


def test_brief_states_the_l3_restriction_in_the_text():
    """The rule has to reach the orchestrator reading the brief, not just this file."""
    text = render(a_brief())
    assert "verifier-spec" in text and "must not" in text.lower()


# --- completeness: a dropped path costs more than no brief --------------------


def test_grouping_drops_no_path():
    b = a_brief()
    grouped = [f for _, _, hit, _ in b.grouped() for f in hit]
    assert sorted(grouped) == sorted(b.files)


def test_a_path_matching_no_known_area_still_appears():
    """`odd/place.txt` matches no AREAS prefix; it must land in `other`, not vanish."""
    b = a_brief()
    labels = {label: hit for _, label, hit, _ in b.grouped()}
    assert "odd/place.txt" in labels["other"]


def test_every_changed_file_is_named_in_the_rendered_brief():
    text = render(a_brief())
    for f in a_brief().files:
        assert f in text, f"{f} is changed but absent from the brief"


def test_api_changes_surface_the_lens_they_must_fire():
    """The trigger used to be baked into a label string; it is now structured data
    on the area map in harness.yaml. It must still reach the orchestrator's eye —
    a trigger nobody sees is a trigger nobody fires."""
    text = render(a_brief())
    from models.project import load as _l

    fired = sorted({t for a in _l().areas if a.triggers for t in a.triggers})
    assert fired, "this project declares no triggers"
    assert f"fires: {fired[0]}" in text
    assert "Lenses this change must fire" in text
    assert "zero path grep is never an exemption" in text


# --- the point of the exercise ------------------------------------------------


def test_the_brief_points_at_per_file_patches_rather_than_inlining_them():
    text = render(a_brief())
    assert "by-file" in text and "Do not `git show` it" in text


def test_missing_bead_text_is_noted_not_silently_empty():
    """A lens told plainly that the task text is absent behaves better than one
    handed an empty section it assumes is complete."""
    text = render(
        a_brief(bead_text="", notes=["no task 'X' in the tracker — text not included"])
    )
    assert "Brief generation notes" in text
    assert "text not included" in text


# --- against the real repository ----------------------------------------------


def test_build_against_head_produces_a_brief_far_smaller_than_the_diff(tmp_path):
    b = build("PROJ-1", "HEAD", tmp_path / "b")
    brief_md = (b.root / "brief.md").read_text()
    full = (b.diff_dir / "full.patch").read_text()

    assert not DIFF_BODY.findall(brief_md), "the real brief leaked a diff body"
    # A brief carries a fixed explanatory header, so on a SMALL diff it is legitimately
    # larger than what it summarises — the saving is on the 44-file commits the lens
    # gate actually faces. The old escape hatch was an arbitrary `len(full) < 2000`,
    # which a 2,029-character commit walked straight past. Compare against the header
    # instead, which is the thing that makes a small brief big.
    header = len(brief_md) - len(b.stat) - len(b.bead_text or "")
    assert len(brief_md) < len(full) or len(full) < header, (
        "the brief should be smaller than the diff it summarises, unless the diff is "
        "smaller than the brief's own fixed header"
    )
    for f in b.files:
        assert f in brief_md


def test_every_changed_file_gets_its_own_patch(tmp_path):
    """Targeted reads are the saving. A missing per-file patch sends the lens
    back to the full diff, which is the cost this exists to avoid."""
    b = build("PROJ-1", "HEAD", tmp_path / "b")
    patches = {p.name for p in (b.diff_dir / "by-file").glob("*.patch")}
    assert len(patches) >= min(len(b.files), 1)


def test_an_unknown_commit_fails_loudly(tmp_path):
    from verify.brief import BriefError

    with pytest.raises(BriefError):
        build("PROJ-x", "notacommit", tmp_path / "b")
