"""Discovery: an enumeration of module × root over the existing classification.

A pure function of a filesystem, so every case is a fixture repository. The fixtures
repoint `models.project.REPO` — the one place `Stack.present` reads — per test, which is the
precedent `test_archive.py` set; conftest's session-wide value is the plugin itself.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

import models.project as project
from models.discover import available, dependencies, discover, listing, roots
from models.resolve import PLUGIN_ROOT


@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setattr(project, "REPO", tmp_path)
    monkeypatch.setattr(project, "PROJECT_STACKS_DIR", tmp_path / ".harness" / "stacks")
    monkeypatch.setattr(
        project, "PROJECT_FRAMEWORKS_DIR", tmp_path / ".harness" / "frameworks"
    )
    return tmp_path


def _touch(repo: Path, *rels: str) -> None:
    for rel in rels:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}\n" if rel.endswith(".json") else "")


def _config(repo: Path, d: dict) -> project.Project:
    path = repo / "harness.yaml"
    path.write_text(yaml.safe_dump({"name": "fx", "slug": "fx", "areas": [], **d}))
    return project.load(path)


def _by_key(found):
    return {f"{f.kind}:{f.key}": f for f in found}


def test_a_root_layout_toolchain_is_present_at_the_root(repo):
    _touch(repo, "pyproject.toml", "uv.lock")
    f = _by_key(discover(None))["stack:python-uv@."]
    assert f.state == "present" and f.evidence == ("uv.lock",)
    assert not f.declared and not f.declined


def test_a_toolchain_in_a_subdirectory_is_found_at_that_root(repo):
    _touch(repo, "web/package.json", "web/package-lock.json")
    found = _by_key(discover(None))
    assert "stack:node-npm@web" in found
    assert found["stack:node-npm@web"].evidence == ("web/package-lock.json",)
    assert "stack:node-npm@." not in found


def test_a_language_marker_alone_is_ambiguous_not_present(repo):
    _touch(repo, "pyproject.toml")
    f = _by_key(discover(None))["stack:python-uv@."]
    assert f.state == "ambiguous" and f.evidence == ("pyproject.toml",)


def test_declared_and_declined_are_reported_per_name_and_root(repo):
    _touch(
        repo,
        "pyproject.toml",
        "uv.lock",
        "web/package-lock.json",
        "tools/package-lock.json",
    )
    p = _config(
        repo,
        {
            "stacks": ["python-uv"],
            "declined": {"stacks": {"node-npm@tools": "a vendored build script"}},
        },
    )
    found = _by_key(discover(p))
    assert found["stack:python-uv@."].declared
    assert found["stack:node-npm@tools"].declined
    assert found["stack:node-npm@tools"].reason == "a vendored build script"
    # Declining one location must not hide another — the reason it is keyed by root.
    assert not found["stack:node-npm@web"].declined
    assert not found["stack:node-npm@web"].declared


def test_no_template_and_no_internal_module_is_ever_offered(repo):
    _touch(repo, "harness/uv.lock", "harness/pyproject.toml", "harness/pytest.ini")
    names = {f.name for f in discover(None)}
    assert "_template" not in available("stack") and "_template" not in available(
        "framework"
    )
    assert "python-uv-selftest" not in available("stack")
    assert "python-uv-selftest" not in names
    assert "python-uv" in names


def test_a_project_local_module_shadows_and_extends_the_shipped_set(repo):
    local = repo / ".harness" / "stacks"
    local.mkdir(parents=True)
    (local / "go-modules.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "go-modules",
                "detect_any": ["go.sum"],
                "dependency_dir": "vendor",
                "commands": {"test": "go test ./..."},
            }
        )
    )
    _touch(repo, "go.sum")
    assert available("stack")["go-modules"] == local / "go-modules.yaml"
    assert _by_key(discover(None))["stack:go-modules@."].state == "present"


def test_this_repository_finds_python_uv_declined_and_never_its_selftest():
    """The harness's own toolchain is internal; the shipped `python-uv` it overlaps with at
    `harness/` is declined in the plugin's own config, with a reason."""
    p = project.load(PLUGIN_ROOT / "harness.yaml")
    found = _by_key(discover(p, repo=PLUGIN_ROOT))
    assert found["stack:python-uv@harness"].declined
    assert all(f.name != "python-uv-selftest" for f in found.values())


def test_frameworks_are_only_ever_candidates_from_a_declared_dependency(repo):
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["Django>=4.2", "django[argon2]>=4", "requests"]\n'
    )
    found = [f for f in discover(None) if f.kind == "framework"]
    assert [(f.name, f.state, f.evidence) for f in found] == [
        ("django", "candidate", ("pyproject.toml",))
    ]
    assert dependencies(".", repo) == {"pyproject.toml": ["django", "requests"]}, (
        "Django and django are one name"
    )


def test_no_framework_module_has_grown_a_detect_key():
    """`frameworks/_template.yaml`: a `detect` key was removed for being inert. Discovery's
    hint is a dependency, so a module must never start carrying markers again."""
    for path in available("framework").values():
        d = yaml.safe_load(path.read_text()) or {}
        assert not {"detect", "detect_any", "detect_language"} & set(d), path.name


def test_discovery_works_with_no_config_at_all(repo):
    _touch(repo, "uv.lock")
    assert not (repo / "harness.yaml").exists()
    assert discover(None)


# --- what setup's agent reads to name what NO module covers -------------------------------


def test_roots_skip_dot_ignored_and_output_directories(repo):
    for d in ("web", ".cache", "node_modules", "generated", "api"):
        (repo / d).mkdir()
    (repo / ".gitignore").write_text("generated/\n")
    found, truncated = roots(repo)
    assert found == (".", "api", "web") and not truncated


def test_roots_report_when_the_cap_cuts_the_list(repo, monkeypatch):
    import models.discover as mod

    monkeypatch.setattr(mod, "ROOT_CAP", 2)
    for d in ("a", "b", "c"):
        (repo / d).mkdir()
    found, truncated = roots(repo)
    assert found == (".", "a", "b") and truncated


def test_listing_names_a_roots_files_and_omits_ignored_ones(repo):
    _touch(repo, "Cargo.toml", "Cargo.lock", "secret.env", "src/main.rs")
    (repo / ".gitignore").write_text("secret.env\n")
    got = listing(".", repo)
    assert {"Cargo.toml", "Cargo.lock", "src/"} <= set(got)
    assert "secret.env" not in got and ".git" not in got


def test_dependencies_are_names_only_from_both_manifest_kinds(repo):
    (repo / "web").mkdir()
    (repo / "web" / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"next": "14.0.0"},
                "devDependencies": {"vitest": "^1"},
            }
        )
    )
    (repo / "pyproject.toml").write_text(
        '[project]\ndependencies = ["fastapi[standard]>=0.110", "pydantic ; python_version>\'3.10\'"]\n'
        '[tool.poetry.dependencies]\npython = "^3.11"\nrich = "*"\n'
    )
    assert dependencies("web", repo) == {"package.json": ["next", "vitest"]}
    assert dependencies(".", repo) == {
        "pyproject.toml": ["fastapi", "pydantic", "rich"]
    }


def test_an_unparseable_manifest_contributes_nothing_rather_than_failing(repo):
    (repo / "package.json").write_text("{ not json")
    (repo / "pyproject.toml").write_text("[[[")
    assert dependencies(".", repo) == {}


def test_the_wrapper_emits_json_with_listings_and_dependencies():
    out = subprocess.run(
        [str(PLUGIN_ROOT / "harness" / "setup" / "discover.sh"), "--json"],
        capture_output=True,
        text=True,
        cwd=str(PLUGIN_ROOT),
        check=True,
    ).stdout
    d = json.loads(out)
    assert {"findings", "roots", "truncated", "listing", "dependencies"} <= set(d)
    assert "harness" in d["roots"] and "harness/" in d["listing"]["."]
