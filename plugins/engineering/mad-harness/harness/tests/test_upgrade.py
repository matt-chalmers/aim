"""How a consuming repository learns the plugin moved.

`claude plugin update` replaces the cache wholesale, ships no hook, and changes nothing in
the project — so a harness.yaml written for 0.9.0 ran under 0.9.1 forever, missing every
block the new version read. The stamp, the comparison and the strict pre-flight exit are
the mechanism; the last three tests are the discipline on the plugin side, so a version
bump cannot ship without saying what it asks of a consumer.
"""

from __future__ import annotations

import re

import pytest

from models import check_project
from models.project import (
    PLUGIN_MANIFEST,
    UPGRADE_NOTES,
    Project,
    ProjectError,
    plugin_version,
    upgrade_status,
)
from models.resolve import PLUGIN_ROOT


def _project(raw: dict) -> Project:
    return Project(name="x", slug="x", stacks=(), paths={}, areas=(), security={}, raw=raw)


# --- the stamp ------------------------------------------------------------------------


def test_the_plugin_version_is_read_from_the_manifest_not_typed():
    import json

    assert plugin_version() == json.loads(PLUGIN_MANIFEST.read_text())["version"]


def test_an_absent_stamp_is_none_not_an_error():
    assert _project({}).stamped_version() is None


def test_a_stamp_is_read_as_a_string_whatever_yaml_made_of_it():
    assert _project({"harness": {"version": 0.9}}).stamped_version() == "0.9"
    assert _project({"harness": {"version": "0.9.1"}}).stamped_version() == "0.9.1"


@pytest.mark.parametrize(
    "raw",
    [
        {"harness": "0.9.1"},  # not a map
        {"harness": {}},  # no version
        {"harness": {"version": ""}},
        {"harness": {"version": "latest"}},  # not dotted numbers
    ],
)
def test_a_malformed_stamp_fails_at_config_time(raw):
    with pytest.raises(ProjectError):
        _project(raw).stamped_version()


@pytest.mark.parametrize(
    ("stamped", "installed", "expected"),
    [
        (None, "0.9.1", "unstamped"),
        ("0.9.0", "0.9.1", "behind"),
        ("0.9.1", "0.9.1", "current"),
        ("0.9.1", "0.10.0", "behind"),  # numeric, not lexical: 10 > 9
        ("0.10.0", "0.9.1", "ahead"),
        ("1.0", "0.9.1", "ahead"),
        ("0.9.1-rc1", "0.9.1", "current"),  # a suffix is dropped, not compared
    ],
)
def test_upgrade_status_orders_versions_numerically(stamped, installed, expected):
    assert upgrade_status(stamped, installed) == expected


# --- the check, and its strict exit ---------------------------------------------------


def _run(monkeypatch, raw: dict, argv: list[str]) -> tuple[int, str, str]:
    """Drive check_project.main against a fake project; return (rc, out, err)."""
    import io
    import sys

    monkeypatch.setattr(check_project, "load", lambda: _project(raw))
    monkeypatch.setattr(check_project, "plugin_version", lambda: "0.9.1")
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    rc = check_project.main(argv)
    return rc, out.getvalue(), err.getvalue()


def test_a_behind_config_is_advisory_by_hand_and_blocks_a_preflight(monkeypatch):
    rc, out, err = _run(monkeypatch, {"harness": {"version": "0.9.0"}}, [])
    assert rc == 0
    assert "UPGRADE:" in err and "0.9.0" in err and "/harness-setup" in err
    assert "UPGRADE PENDING" in out, "the summary must not say OK under an UPGRADE line"

    rc, out, _ = _run(monkeypatch, {"harness": {"version": "0.9.0"}}, ["--strict"])
    assert rc == check_project.EXIT_UPGRADE
    assert "BLOCKED" in out


def test_an_unstamped_config_is_treated_as_behind(monkeypatch):
    rc, _, err = _run(monkeypatch, {}, ["--strict"])
    assert rc == check_project.EXIT_UPGRADE
    assert "no `harness.version`" in err


def test_a_current_config_passes_strict(monkeypatch):
    rc, out, err = _run(monkeypatch, {"harness": {"version": "0.9.1"}}, ["--strict"])
    assert rc == 0
    assert "UPGRADE" not in err and "harness: 0.9.1  installed plugin 0.9.1" in out


def test_a_config_ahead_of_the_plugin_blames_the_plugin_not_the_config(monkeypatch):
    rc, _, err = _run(monkeypatch, {"harness": {"version": "0.10.0"}}, ["--strict"])
    assert rc == 0, "the config is fine; it is the plugin that needs updating"
    assert "claude plugin update" in err


def test_strict_does_not_promote_ordinary_warnings(monkeypatch):
    """Only the upgrade finding blocks. A stale area path is a warning, not a stop —
    otherwise --strict would halt every campaign over an advisory."""
    from models.project import Area

    proj = Project(
        name="x", slug="x", stacks=(), paths={}, security={},
        areas=(Area(path="no/such/dir", label="gone", triggers=()),),
        raw={"harness": {"version": "0.9.1"}},
    )
    monkeypatch.setattr(check_project, "load", lambda: proj)
    monkeypatch.setattr(check_project, "plugin_version", lambda: "0.9.1")
    import io
    import sys

    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    rc = check_project.main(["--strict"])
    assert "does not exist" in err.getvalue(), "the warning fired"
    assert rc == 0, "and did not block"


# --- the discipline on the plugin side --------------------------------------------------


def test_every_version_has_upgrade_notes():
    """A bump cannot ship without a section saying what it asks of a consumer — even if
    that is "nothing"."""
    text = UPGRADE_NOTES.read_text()
    v = plugin_version()
    assert re.search(rf"^### {re.escape(v)}\s*$", text, re.M), (
        f"docs/upgrading.md has no `### {v}` section for the current plugin version"
    )


def test_the_template_is_stamped_with_the_current_version():
    import yaml

    raw = yaml.safe_load((PLUGIN_ROOT / "templates" / "harness.yaml.example").read_text())
    assert str(raw["harness"]["version"]) == plugin_version()


def test_the_harness_stamps_its_own_config():
    """The harness is its own first consumer, so its config is stamped like anyone's —
    and a bump that forgets it fails here rather than as a pre-flight surprise."""
    import yaml

    raw = yaml.safe_load((PLUGIN_ROOT / "harness.yaml").read_text())
    assert str(raw["harness"]["version"]) == plugin_version()
