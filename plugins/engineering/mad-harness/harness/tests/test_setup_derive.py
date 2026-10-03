"""Setup's derivations: what the repository already says, with where it said it — values the
writer may use, candidates it may not, and what only the owner can answer."""

from __future__ import annotations

import json
import subprocess

import pytest
import yaml

import models.check_commands as check_commands
import models.project as project
from models import setup_derive as sd


@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for mod in (project, check_commands):
        monkeypatch.setattr(mod, "REPO", tmp_path)
    monkeypatch.setattr(project, "PROJECT_FILE", tmp_path / "harness.yaml")
    monkeypatch.setattr(project, "PROJECT_STACKS_DIR", tmp_path / ".harness" / "stacks")
    monkeypatch.setattr(
        project, "PROJECT_FRAMEWORKS_DIR", tmp_path / ".harness" / "frameworks"
    )
    return tmp_path


def _write(repo, rel, text=""):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _ctx(runner=None):
    return sd.context(runner=runner)


# --- identity ---------------------------------------------------------------------------


def test_the_name_comes_from_the_remote_first_and_every_other_source_is_offered(repo):
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "remote",
            "add",
            "origin",
            "git@github.com:acme/Tip-Donkey.git",
        ],
        check=True,
    )
    _write(repo, "package.json", json.dumps({"name": "@acme/tipdonkey-web"}))
    _write(repo, "README.md", "# Tip Donkey\n\nprose")
    d = sd.derive_identity(_ctx())
    assert d.values == {"name": "Tip-Donkey", "slug": "tipdonkey"}
    assert d.evidence["name"] == "git remote"
    offered = {c["source"]: c["value"] for c in d.candidates["name"]}
    assert (
        offered["package.json"] == "tipdonkey-web"
        and offered["README.md"] == "Tip Donkey"
    )


def test_a_declared_name_and_slug_are_kept(repo):
    _write(
        repo,
        "harness.yaml",
        yaml.safe_dump({"name": "Mine", "slug": "mine", "areas": []}),
    )
    d = sd.derive_identity(_ctx())
    assert (
        d.values == {"name": "Mine", "slug": "mine"}
        and d.evidence["slug"] == "already declared"
    )


def test_a_name_with_nothing_to_build_a_slug_from_owes_one(repo, monkeypatch):
    monkeypatch.setattr(sd, "_name_sources", lambda repo: [("—", "README.md")])
    d = sd.derive_identity(_ctx())
    assert "slug" not in d.values and any("slug" in o for o in d.owed)


# --- stacks and frameworks ----------------------------------------------------------------


def test_present_toolchains_are_adopted_at_their_root_and_ambiguous_ones_owed(repo):
    _write(repo, "uv.lock")
    _write(repo, "pyproject.toml", "[project]\nname='x'\n")
    _write(repo, "web/package-lock.json")
    _write(repo, "tools/pyproject.toml")
    d = sd.derive_stacks(_ctx())
    assert "python-uv" in d.values["stacks"]
    assert {"name": "node-npm", "root": "web"} in d.values["stacks"]
    assert any("python-uv at tools" in o for o in d.owed)
    assert (
        "web/" in d.evidence["listing"]["."]
        and "package-lock.json" in d.evidence["listing"]["web"]
    )


def test_a_declared_stack_keeps_its_overrides_and_a_vanished_one_is_owed(repo):
    _write(repo, "uv.lock")
    _write(
        repo,
        "harness.yaml",
        yaml.safe_dump(
            {
                "name": "x",
                "slug": "x",
                "areas": [],
                "stacks": [
                    {"name": "python-uv", "commands": {"test": "uv run pytest -x"}},
                    {"name": "node-npm", "root": "gone"},
                ],
            }
        ),
    )
    d = sd.derive_stacks(_ctx())
    assert {"name": "python-uv", "commands": {"test": "uv run pytest -x"}} in d.values[
        "stacks"
    ]
    assert any(
        "node-npm@gone is declared but its markers are absent" in o for o in d.owed
    )


def test_a_declined_toolchain_is_not_adopted(repo):
    _write(repo, "tools/package-lock.json")
    _write(
        repo,
        "harness.yaml",
        yaml.safe_dump(
            {
                "name": "x",
                "slug": "x",
                "areas": [],
                "declined": {"stacks": {"node-npm@tools": "vendored"}},
            }
        ),
    )
    d = sd.derive_stacks(_ctx())
    assert d.values["stacks"] == [] and d.values["declined"]["stacks"] == {
        "node-npm@tools": "vendored"
    }


def test_frameworks_are_candidates_never_values_and_dependencies_are_handed_over(repo):
    _write(
        repo, "pyproject.toml", '[project]\ndependencies = ["django>=4", "celery"]\n'
    )
    d = sd.derive_frameworks(_ctx())
    assert d.values == {"frameworks": []} and d.declinable
    assert d.candidates["frameworks"] == [
        {"value": "django", "source": "pyproject.toml"}
    ]
    assert d.evidence["dependencies"] == {".": {"pyproject.toml": ["celery", "django"]}}


# --- commands ---------------------------------------------------------------------------


def _local_module(repo, commands):
    _write(
        repo,
        ".harness/stacks/fake-tool.yaml",
        yaml.safe_dump(
            {
                "name": "fake-tool",
                "detect_any": ["fake.lock"],
                "dependency_dir": "deps",
                "commands": commands,
            }
        ),
    )
    _write(repo, "fake.lock")


def _runner(passing: str):
    def run(command, **kw):
        return subprocess.CompletedProcess(
            command, 0 if passing in command else 1, "", "boom"
        )

    return run


def test_a_command_that_verifies_is_left_alone(repo):
    _local_module(repo, {"verify": "true --version", "test": "true"})
    ctx = _ctx(runner=_runner("true"))
    ctx.stacks = ["fake-tool"]
    d = sd.derive_commands(ctx)
    assert (
        d.values["stacks"] == ["fake-tool"]
        and d.evidence["commands.fake-tool@."] == "verify clean"
    )


def test_a_rotted_command_is_repaired_by_proof_and_nothing_is_written(repo):
    _local_module(repo, {"verify": "true check", "test": "true old-test"})
    _write(repo, "Makefile", "test:\n\ttrue\n")
    ctx = _ctx(runner=_runner("make"))
    ctx.stacks = ["fake-tool"]
    d = sd.derive_commands(ctx)
    # `verify` failed, so the key workers run (`test`) is the one repaired — by a Makefile
    # target the repository already wrote, proved by running it.
    assert d.values["stacks"] == [
        {"name": "fake-tool", "commands": {"test": "make test"}}
    ]
    assert "proved by running" in d.evidence["commands.fake-tool@.:test"]
    assert not (repo / "harness.yaml").exists(), "derivation writes nothing"


def test_a_command_nothing_can_repair_is_owed(repo):
    _local_module(repo, {"verify": "true check", "test": "true"})
    ctx = _ctx(runner=_runner("nothing-passes"))
    ctx.stacks = ["fake-tool"]
    d = sd.derive_commands(ctx)
    assert d.owed and "none worked" in d.owed[0]


# --- paths and tracker --------------------------------------------------------------------


def test_paths_keep_only_roles_that_exist_and_owe_the_rest(repo):
    _write(repo, "docs/INDEX.md")
    _write(repo, "src/api/x.py")
    d = sd.derive_paths(_ctx())
    assert d.values["paths"] == {"docs": "docs", "index": "docs/INDEX.md"}
    assert d.values["layout"] == {"roles": {"api": "src/api"}}
    assert any(o.startswith("paths.adrs") for o in d.owed)


def test_the_tracker_block_derives_a_prefix_and_slot_and_offers_missing_gitignore_lines(
    repo, monkeypatch
):
    monkeypatch.setattr(sd, "_beads_installed", lambda: True)
    _write(repo, ".gitignore", ".claude/\n**/.harness/run/\n")
    _write(
        repo, "harness.yaml", yaml.safe_dump({"name": "Fx", "slug": "fx", "areas": []})
    )
    d = sd.derive_tracker(_ctx())
    assert d.values["tracker"] == {"backend": "beads"}
    assert d.values["beads"] == {"prefix": "FX"} and d.values["swarm"] == {
        "merge_slot": "FX-merge-slot"
    }
    missing = [c["value"] for c in d.candidates["gitignore"]]
    assert ".claude/worktrees/" not in missing and ".harness/run/" not in missing, (
        "broader rules count"
    )
    assert missing == [".harness/cache/", ".harness/tasks/", ".swarm*"]


def test_an_existing_beads_prefix_wins_over_the_slug(repo):
    _write(repo, ".beads/config.yaml", "issue-prefix: TD\n")
    d = sd.derive_tracker(_ctx())
    assert (
        d.values["beads"]["prefix"] == "TD"
        and d.evidence["beads.prefix"] == ".beads/config.yaml"
    )


def test_no_tracker_binary_and_no_store_owes_the_choice(repo, monkeypatch):
    monkeypatch.setattr(sd, "_beads_installed", lambda: False)
    d = sd.derive_tracker(_ctx())
    assert "tracker" not in d.values and any(
        "beads is not installed" in o for o in d.owed
    )


def test_derive_all_hands_the_stacks_decision_to_later_blocks(repo):
    _write(repo, "uv.lock")
    ctx = _ctx(runner=_runner("never"))
    drafts = sd.derive_all(ctx)
    assert [d.block for d in drafts][:6] == [
        "identity",
        "stacks",
        "frameworks",
        "commands",
        "paths",
        "tracker",
    ]
    assert ctx.stacks == ["python-uv"]


# --- blocks 7-12 --------------------------------------------------------------------------


def _commit_all(repo, message="init"):
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            message,
        ],
        check=True,
    )


def test_security_writes_nothing_and_never_surfaces_a_secret_value(repo):
    _write(
        repo,
        ".env.example",
        "DATABASE_URL=postgres://user:hunter2@db/x\nexport STRIPE_KEY=sk_live_abc\n",
    )
    _write(repo, "src/auth/login.py")
    _write(repo, "src/widgets/w.py")
    _commit_all(repo)
    d = sd.derive_security(_ctx())
    assert d.values == {}
    assert {"value": "src/auth/", "source": "path"} in d.candidates["security.paths"]
    assert all("widgets" not in c["value"] for c in d.candidates["security.paths"])
    tokens = d.candidates["security.tokens"]
    assert [c["value"] for c in tokens] == ["DATABASE_URL", "STRIPE_KEY"]
    assert tokens[0]["source"] == ".env.example:1"
    dumped = json.dumps(d.candidates)
    assert "hunter2" not in dumped and "sk_live" not in dumped
    assert len(d.owed) == 3


def test_areas_follow_churn_and_carry_the_security_trigger_where_a_path_is_secure(repo):
    _write(
        repo,
        "harness.yaml",
        yaml.safe_dump(
            {
                "name": "x",
                "slug": "x",
                "areas": [],
                "security": {"paths": ["src/auth/"]},
            }
        ),
    )
    for n in range(3):
        _write(repo, f"src/auth/f{n}.py", str(n))
        _commit_all(repo, f"c{n}")
    _write(repo, "docs/a.md", "x")
    _commit_all(repo, "docs")
    raw = yaml.safe_load((repo / "harness.yaml").read_text())
    raw.pop("areas")
    (repo / "harness.yaml").write_text(yaml.safe_dump({**raw, "areas": []}))
    d = sd.derive_areas(_ctx())
    by_path = {a["path"]: a for a in d.values["areas"]}
    assert by_path["src"]["triggers"] == ["security"], "src contains a secured path"
    assert "triggers" not in by_path["docs"]
    assert list(by_path)[0] == "src", "ordered by churn"


def test_testing_finds_layout_and_aggregates_and_owes_gates_and_coverage(repo):
    _write(repo, "tests/test_a.py")
    _write(repo, "web/a.test.ts")
    _write(repo, "Makefile", "test:\n\ttrue\nlint:\n\ttrue\nbuild:\n\ttrue\n")
    _commit_all(repo)
    d = sd.derive_testing(_ctx())
    assert any("`tests/`" in line for line in d.values["testing"]["layout"]["Tests"])
    assert d.values["testing"]["aggregate_commands"] == {
        "Whole repo": "`make test` - `make lint`"
    }
    assert d.values["lenses"] == {"additional": []}
    assert sorted(o.split(" ")[0] for o in d.owed) == [
        "testing.coverage",
        "testing.gates",
    ]


def test_lanes_one_per_stack_with_an_unmeasured_cap_and_ports_only_as_candidates(repo):
    _write(
        repo,
        "docker-compose.yml",
        'services:\n  web:\n    ports:\n      - "3000:3000"\n',
    )
    _write(repo, ".env.example", "API_PORT=8000\n")
    _commit_all(repo)
    ctx = _ctx()
    ctx.stacks = ["python-uv", {"name": "node-npm", "root": "web"}]
    d = sd.derive_lanes(ctx)
    assert set(d.values["lanes"]) == {"python-uv", "web", "docs"}
    assert "unmeasured" in d.values["lanes"]["web"]["constraint"]
    assert "ports" not in d.values, "ports are declared by a human, never scraped"
    sources = {(c["value"], c["source"]) for c in d.candidates["ports"]}
    assert (3000, "docker-compose.yml:4") in sources and (
        8000,
        ".env.example:1",
    ) in sources
    assert any(o.startswith("fidelity") for o in d.owed) and d.declinable


def test_domain_and_signals_are_measured_from_this_repository(repo):
    _write(repo, "a.py", "\n" * 50)
    _commit_all(repo, "feat: a")
    _write(repo, "b.py", "x")
    _commit_all(repo, "fix: b")
    d = sd.derive_domain(_ctx())
    assert d.values["domain"]["nouns"] == []
    base = d.values["signals"]["baselines"]
    assert base["escape_rate"] == 0.5 and base["first_pass_floor"] == 0.40
    assert d.values["signals"]["megafile_lines"] == 500, "floored"


def test_models_writes_nothing_and_says_why(repo):
    d = sd.derive_models(_ctx())
    assert d.values == {} and "shipped" in d.evidence["models"]


def test_derive_all_covers_blocks_one_to_twelve(repo):
    _write(repo, "uv.lock")
    drafts = sd.derive_all(_ctx(runner=_runner("never")))
    from models.setup_blocks import BLOCKS

    assert [d.block for d in drafts] == [b.id for b in BLOCKS if b.id != "stamp"]


# --- the stack breakout -------------------------------------------------------------------


def test_the_breakout_offers_markers_a_ranked_dependency_dir_and_a_restore_command(
    repo,
):
    _write(repo, "go.mod", "module x\n")
    _write(repo, "go.sum")
    _write(repo, ".gitignore", "vendor/\nbuild/\n")
    _write(repo, "vendor/big/a.bin", "x" * 300_000)
    _write(repo, "build/small", "x")
    _write(
        repo,
        ".github/workflows/ci.yml",
        "jobs:\n  t:\n    steps:\n      - run: go mod download\n      - run: go test ./...\n",
    )
    _write(repo, ".env.example", "GOCACHE_DIR=/tmp/x\n")
    _commit_all(repo)
    d = sd.derive_stack_breakout(".", _ctx())
    markers = [c["value"] for c in d.candidates["detect_any"]]
    assert "go.mod" in markers and "go.sum" in markers
    assert d.values["dependency_dir"] == "vendor", (
        "the largest gitignored directory leads"
    )
    assert [c["value"] for c in d.candidates["bootstrap.command"]] == [
        "go mod download"
    ]
    assert d.candidates["env"] == [{"value": "GOCACHE_DIR", "source": ".env.example"}]
    assert any(o.startswith("banned_forms") for o in d.owed)


def _worktrees(repo):
    return subprocess.run(
        ["git", "-C", str(repo), "worktree", "list"], capture_output=True, text=True
    ).stdout


def test_bootstrap_timing_measures_and_always_removes_its_worktree(repo):
    _write(repo, "a")
    _commit_all(repo)
    before = _worktrees(repo)
    ok = sd.time_bootstrap("true", repo=repo)
    assert ok["ok"] and ok["seconds"] is not None and ok["recommend"] == "install"
    failed = sd.time_bootstrap("exit 3", repo=repo)
    assert not failed["ok"]
    slow = sd.time_bootstrap("sleep 5", repo=repo, timeout=1)
    assert not slow["ok"] and slow["detail"] == "timed out"
    assert _worktrees(repo) == before, "no scratch worktree left behind"


def test_a_slow_restore_recommends_a_symlink_and_says_so(repo, monkeypatch):
    _write(repo, "a")
    _commit_all(repo)
    monkeypatch.setattr(sd, "INSTALL_SECONDS", 0)
    got = sd.time_bootstrap("true", repo=repo)
    assert got["recommend"] == "symlink" and got["threshold"] == 0
