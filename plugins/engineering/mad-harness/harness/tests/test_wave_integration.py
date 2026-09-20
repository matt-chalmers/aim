"""The commands the prompts actually tell an agent to run, run.

WHY THIS EXISTS. Every other test here drives the port directly. Nothing checked that the
lines an agent READS are lines it can EXECUTE — and a prompt is where a typo costs the
most, because the failure surfaces inside a dispatched worker as a stalled or confused
agent rather than as a red test.

Two levels:

* every `tk.sh` invocation in the corpus PARSES against the real CLI, placeholders and
  all. A verb that does not exist, or a flag spelled the way bd spelled it, fails here
  instead of mid-wave.
* the pre-flight and wave sequence from `campaign-loop` and `/swarm` RUNS end to end
  against a scratch repository, in order, on the markdown backend.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from models.resolve import PLUGIN_ROOT
from tracker.cli import build_parser

# Prompts write `${CLAUDE_PLUGIN_ROOT}/harness/...`, which the plugin loader expands.
# `$HARNESS_ROOT` is accepted here only so a half-converted tree still reports rather than
# silently scanning nothing — which is what happened when the corpus was converted and
# these patterns were not.
_ROOT = r'(?:\$\{CLAUDE_PLUGIN_ROOT\}/harness|\$HARNESS_ROOT)'
TK = re.compile(_ROOT + r'/tracker/tk\.sh\s+([^`\n|;)&]+)')
DISPATCH = re.compile(_ROOT + r'/models/dispatch\.sh\s+([^`\n|;)&]+)')


def _corpus() -> list[str]:
    """Tracked prompt files that still EXIST.

    `git ls-files` reports the index, and these run at COLLECTION time — so a file
    deleted but not yet staged took the whole suite down with an unreadable-path error
    before a single test ran. A mid-edit tree is a normal state; the suite must survive
    one.
    """
    listed = subprocess.run(
        ["git", "ls-files", "agents", "commands", "skills"],
        capture_output=True, text=True, cwd=PLUGIN_ROOT,
    ).stdout.split()
    return [f for f in listed if (Path(PLUGIN_ROOT) / f).is_file()]


def _invocations() -> list[tuple[str, int, list[str]]]:
    """Complete commands from FENCED blocks — the lines an agent runs verbatim.

    Deliberately NOT every mention. Prose refers to commands by name — "`tk.sh close`
    stops keeping the jsonl fresh" — and those are references, not invocations: `close`
    without `--reason` is correctly unparseable and flagging it would be flagging English.
    The distinction that matters is whether an agent would type the line.
    """
    import shlex

    out = []
    for rel in _corpus():
        infence = False
        for i, line in enumerate((Path(PLUGIN_ROOT) / rel).read_text().splitlines(), 1):
            if line.lstrip().startswith("```"):
                infence = not infence
                continue
            if not infence or line.lstrip().startswith("#"):
                continue
            m = TK.search(line)
            if not m:
                continue
            text = m.group(1).split("  #")[0].split("\t#")[0]
            try:
                argv = shlex.split(text)
            except ValueError:
                continue  # a quote left open means the command wraps onto the next line
            if argv:
                out.append((rel, i, argv))
    return out


def test_the_corpus_actually_contains_invocations():
    """A parser test over an empty set passes having checked nothing."""
    assert len(_invocations()) > 25


@pytest.mark.parametrize("site", _invocations(), ids=lambda s: f"{s[0]}:{s[1]}")
def test_every_documented_invocation_parses(site):
    """A prompt that names a verb the CLI does not have fails inside a worker, silently,
    as an agent that cannot do its job — not as a red test. So it fails here."""
    rel, line, argv = site
    parser = build_parser()
    try:
        parser.parse_args(argv)
    except SystemExit:
        pytest.fail(f"{rel}:{line}: `tk.sh {' '.join(argv)}` does not parse")


# --- the sequence, run for real -----------------------------------------------


@pytest.fixture
def project(tmp_path):
    """A scratch repository configured for the markdown backend."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"],
                   cwd=repo, check=True, capture_output=True)
    (repo / "harness.yaml").write_text(
        "name: Trial\nslug: trial\n"
        "areas: [{path: src, label: code}]\n"
        "paths: {proposed: docs/proposed}\n"
        "tracker: {backend: mdfiles, dir: .harness/tasks, export: docs/tasks,"
        " limits: {record_bytes: null}}\n"
    )
    return repo


def _tk(repo: Path, *argv: str, actor: str = "swarm-w1") -> subprocess.CompletedProcess:
    import os
    import sys

    env = {**os.environ, "MAD_HARNESS_REPO": str(repo), "TRACKER_ACTOR": actor,
           "HARNESS_RUN_DIR": str(repo / ".harness" / "run")}
    return subprocess.run(
        [sys.executable, "-m", "tracker.cli", *argv],
        cwd=PLUGIN_ROOT / "harness", env=env, capture_output=True, text=True,
    )


def test_the_preflight_and_wave_sequence_runs_end_to_end(project):
    """The order campaign-loop §0 and /swarm steps 2-9 actually prescribe."""
    r = _tk(project, "autosync", "off")
    assert r.returncode == 0, r.stderr

    assert _tk(project, "slot-check", "--json").returncode == 0

    epic = _tk(project, "create", "an epic", "-t", "epic").stdout.strip()
    a = _tk(project, "create", "first task", "--parent", epic).stdout.strip()
    b = _tk(project, "create", "second task", "--parent", epic).stdout.strip()
    assert _tk(project, "dep", b, a).returncode == 0

    v = _tk(project, "validate", epic)
    assert v.returncode == 0, v.stderr

    ready = _tk(project, "ready", "--parent", epic, "--json").stdout
    assert a in ready and b not in ready, "the blocked task must not be offered"

    # a worker claims, works, takes the slot, closes
    assert _tk(project, "claim", a).returncode == 0
    assert _tk(project, "claim", a, actor="swarm-w2").returncode == 1, (
        "a second worker must be refused the same task"
    )
    assert _tk(project, "slot-acquire").returncode == 0
    assert _tk(project, "note", a, "tests green, 12 mutants, 11 killed").returncode == 0
    assert _tk(project, "close", a, "--reason", "landed").returncode == 0
    assert _tk(project, "slot-release").returncode == 0

    assert b in _tk(project, "ready", "--parent", epic, "--json").stdout, (
        "closing the blocker must release its dependent"
    )

    # wave close-out: export, view, telemetry, autosync back on
    assert _tk(project, "export").returncode == 0
    view = project / "docs" / "proposed" / f"{epic}-trial" / "tasks.md"
    assert _tk(project, "render", epic, "--write", str(view)).returncode == 0
    assert "second task" in view.read_text()
    assert _tk(project, "render", epic, "--check", "--write", str(view)).returncode == 0

    assert _tk(project, "event", "campaign.epic_closed", epic, '{"wave_yield": 83}').returncode == 0
    assert "83" in _tk(project, "events", "--category", "campaign.epic_closed").stdout
    assert _tk(project, "autosync", "on").returncode == 0


def test_the_tracked_export_is_reviewable_markdown(project):
    """The single thing the markdown backend buys. If the export were a blob this whole
    backend would be cost without benefit."""
    epic = _tk(project, "create", "an epic", "-t", "epic").stdout.strip()
    _tk(project, "create", "a task with a real title", "--parent", epic)
    _tk(project, "export")
    files = list((project / "docs" / "tasks").glob("*.md"))
    assert files, "export produced no markdown"
    joined = "\n".join(f.read_text() for f in files)
    assert "a task with a real title" in joined
    assert "---" in joined, "frontmatter, so the fields are machine-readable too"


def test_a_lens_cannot_mutate_the_tracker(project):
    """The read-only guarantee, end to end through the shim a lens actually calls."""
    epic = _tk(project, "create", "an epic", "-t", "epic").stdout.strip()
    assert _tk(project, "--readonly", "show", epic).returncode == 0
    assert _tk(project, "--readonly", "close", epic, "--reason", "nope").returncode == 4


# --- the dispatch boundary's own flags ----------------------------------------


def _dispatch_invocations() -> list[tuple[str, int, list[str]]]:
    import shlex

    out = []
    for rel in _corpus():
        infence = False
        for i, line in enumerate((Path(PLUGIN_ROOT) / rel).read_text().splitlines(), 1):
            if line.lstrip().startswith("```"):
                infence = not infence
                continue
            if not infence or line.lstrip().startswith("#"):
                continue
            m = DISPATCH.search(line)
            if not m:
                continue
            try:
                argv = shlex.split(m.group(1).split("  #")[0])
            except ValueError:
                continue
            if argv:
                out.append((rel, i, argv))
    return out


@pytest.mark.parametrize(
    "site", _dispatch_invocations(), ids=lambda s: f"{s[0]}:{s[1]}"
)
def test_every_documented_dispatch_parses(site):
    """`dispatch.sh` is the other command prompts tell an agent to run, and its flags were
    unguarded.

    A vocabulary sweep renamed `--bead` to `--task` — a PUBLIC CLI FLAG, changed as a side
    effect of a prose pass. It stayed consistent by luck, and nothing checked: the tk.sh
    parser test covers only tk.sh, so a prompt left on the old spelling would have failed
    inside a dispatched worker as an argparse usage error — invisible from the
    orchestrator, which sees an agent that did nothing.
    """
    rel, line, argv = site
    from models.dispatch import build_parser

    # THE REAL PARSER, not a copy of its flags: a copy is the drift this test exists to
    # catch. Prose placeholders are normalised first: `<n>` for a typed integer, and an
    # `[--optional <arg>]` group dropped — the notation, not the flag, is what argparse
    # cannot read.
    norm: list[str] = []
    skipping = False
    for tok in argv:
        if tok.startswith("["):
            skipping = not tok.endswith("]")
            continue
        if skipping:
            skipping = not tok.endswith("]")
            continue
        norm.append("1" if tok in ("<n>", "<N>") else tok)
    try:
        build_parser().parse_args(norm)
    except SystemExit:
        pytest.fail(f"{rel}:{line}: `dispatch.sh {' '.join(argv)}` does not parse")
