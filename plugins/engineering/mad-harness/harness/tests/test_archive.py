"""Archiving a spent epic folder, and the invariant that keeps it from breaking sweeps.

The archive is safe ONLY because of where it sits. Four sweeps glob the staging root or
the docs tree, and the sharpest is the decision register's contention pass: an archive
inside `paths.proposed` would make every retired proposal a live contender and report
`lands_in` clashes forever. So the location is asserted, not trusted.
"""

from __future__ import annotations

import subprocess

import pytest

from models.project import Area, Project, ProjectError


def _project(paths: dict, areas=()):
    return Project(
        name="p", slug="p", stacks=(), paths=paths,
        areas=tuple(Area(path=a, label=a) for a in areas),
        security={}, raw={"paths": paths},
    )


def test_no_archive_declared_means_todays_behaviour():
    assert _project({"proposed": "docs/proposed"}).archive_dir() is None


def test_an_archive_inside_the_staging_root_is_refused():
    """The contention pass globs `<proposed>/*/proposal.md`. An archive there turns every
    retired proposal into a live contender, permanently."""
    with pytest.raises(ProjectError, match="overlaps paths.proposed"):
        _project({"proposed": "docs/proposed",
                  "archive": "docs/proposed/archive"}).archive_dir()


def test_an_archive_inside_the_docs_tree_is_refused():
    """check-doc-drift sweeps `$DOCS_DIR/**/*.md` — an archive there is an ever-growing
    set of dead hits, and a check that cries wolf gets ignored."""
    with pytest.raises(ProjectError, match="overlaps paths.docs"):
        _project({"docs": "docs", "archive": "docs/archive"}).archive_dir()


def test_an_archive_overlapping_an_area_is_refused():
    with pytest.raises(ProjectError, match="overlaps the area"):
        _project({"archive": "src/history"}, areas=("src",)).archive_dir()


def test_an_area_nested_inside_the_archive_is_also_refused():
    """Containment in EITHER direction breaks the sweep; only one direction is obvious."""
    with pytest.raises(ProjectError, match="overlaps the area"):
        _project({"archive": "history"}, areas=("history/src",)).archive_dir()


def test_a_properly_separated_archive_is_accepted():
    p = _project({"docs": "docs", "proposed": "docs/proposed", "archive": ".spec-archive"},
                 areas=("src", "web"))
    assert p.archive_dir() == ".spec-archive"


# --- the move itself ----------------------------------------------------------


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    (r / "docs" / "proposed" / "E-1-widgets").mkdir(parents=True)
    (r / "docs" / "proposed" / "E-1-widgets" / "proposal.md").write_text(
        "---\nstatus: folded-in\n---\n\nThe widget must retry three times.\n"
    )
    (r / "docs" / "proposed" / "E-1-widgets" / "design.md").write_text("A design.\n")
    (r / "harness.yaml").write_text(
        "name: T\nslug: t\nareas: [{path: src, label: code}]\n"
        "paths: {docs: docs, proposed: docs/proposed, archive: .spec-archive}\n"
    )
    subprocess.run(["git", "init", "-q", "."], cwd=r, check=True)
    subprocess.run(["git", "add", "-A"], cwd=r, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "init"], cwd=r, check=True, capture_output=True)
    monkeypatch.setenv("MAD_HARNESS_REPO", str(r))
    monkeypatch.setattr("models.resolve.REPO", r)
    monkeypatch.setattr("models.project.REPO", r)
    monkeypatch.setattr("models.project.PROJECT_FILE", r / "harness.yaml")
    import tracker.archive as mod
    monkeypatch.setattr(mod, "REPO", r)
    return r


def test_the_folder_moves_dated_and_the_staging_root_is_left_empty(repo):
    from tracker.archive import archive_epic

    dest = archive_epic("E-1")
    assert dest.exists() and dest.name.endswith("E-1-widgets")
    assert dest.name[:4].isdigit(), "dated, so the archive reads chronologically"
    assert not list((repo / "docs" / "proposed").glob("E-1*")), (
        "close-out asserts the staging folder is empty; a leftover trips it"
    )


def test_every_archived_file_says_it_is_archived_in_the_file(repo):
    """The directory says it is history, but a file travels — a reader who opens one
    elsewhere must not have to know where it came from."""
    from tracker.archive import archive_epic

    dest = archive_epic("E-1")
    proposal = (dest / "proposal.md").read_text()
    assert "status: archived" in proposal and "archived_from_epic: E-1" in proposal
    assert "The widget must retry three times." in proposal, "content is preserved"


def test_a_file_without_frontmatter_gains_it(repo):
    from tracker.archive import archive_epic

    dest = archive_epic("E-1")
    assert (dest / "design.md").read_text().startswith("---\nstatus: archived")
    assert "A design." in (dest / "design.md").read_text()


def test_archiving_twice_is_refused_rather_than_silently_merging(repo):
    from tracker.archive import archive_epic

    archive_epic("E-1")
    (repo / "docs" / "proposed" / "E-1-widgets").mkdir(parents=True)
    (repo / "docs" / "proposed" / "E-1-widgets" / "x.md").write_text("later\n")
    with pytest.raises(ProjectError, match="already"):
        archive_epic("E-1")


def test_archiving_without_a_declared_archive_is_refused(repo):
    (repo / "harness.yaml").write_text(
        "name: T\nslug: t\nareas: [{path: src, label: code}]\n"
        "paths: {docs: docs, proposed: docs/proposed}\n"
    )
    from tracker.archive import archive_epic

    with pytest.raises(ProjectError, match="paths.archive is not declared"):
        archive_epic("E-1")
