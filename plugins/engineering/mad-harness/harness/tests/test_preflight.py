"""The pre-flight as one call: campaign-loop §0's gates, in its order, judged as it would.

Measured: a field orchestrator's context averaged ~380k tokens over 237 requests, so the
six shell lines of §0 run one per tool call cost ~$0.11-0.17 each to learn an exit
status. The sequence is deterministic, so it is a script — and the tests here drive it
with an injected runner, because the SEQUENCE is what is under test: no git, no tracker
and no config check run behind these.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from models import preflight as mod
from models.check_project import EXIT_UPGRADE
from models.steps import FAIL, INFO, OK, SKIP, UPGR

FREE = json.dumps({"free": True, "holder": "", "stale": False})
HELD = json.dumps({"free": False, "holder": "campaign", "stale": True})
CLEAN_CONFIG = (0, "project: T (t)\n\nOK — config valid, 1 stacks present, 2 areas, hard-coded sites agree.\n", "")
UPGRADE = (
    EXIT_UPGRADE,
    "project: T (t)\nharness: 0.9.4  installed plugin 0.10.9\n\nBLOCKED — the config predates the installed plugin; see UPGRADE above.\n",
    "\nUPGRADE: harness.yaml was written for plugin 0.9.4; 0.10.9 is installed. Run /harness-setup — it applies the upgrade notes between the two and re-stamps the config.\n",
)


def key(argv: list[str]) -> str:
    """`git status`, `tk.sh slot-check`, `check-ports.sh`, `df` — what a call IS."""
    name = Path(argv[0]).name
    return f"{name} {argv[1]}" if name in ("git", "tk.sh") else name


class Runner:
    """Answers each step from a table; records every call in order."""

    def __init__(self, **answers):
        self.answers = {
            "git status": (0, "", ""),
            "check-project-config.sh": CLEAN_CONFIG,
            "tk.sh slot-check": (0, FREE + "\n", ""),
            "tk.sh autosync": (0, "", ""),
            "check-ports.sh": (0, "project: T\nOK — none of the 2 declared port(s) is bound.\n", ""),
            "df": (0, "Filesystem  Size  Used  Avail  Use%\n/dev/disk3  926Gi  650Gi  276Gi  71%\n", ""),
        }
        self.answers.update({k.replace("_", " "): v for k, v in answers.items()})
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        self.cwds = getattr(self, "cwds", []) + [kw.get("cwd")]
        answer = self.answers[key(argv)]
        if isinstance(answer, BaseException):
            raise answer
        rc, out, err = answer
        return subprocess.CompletedProcess(argv, rc, out, err)

    def keys(self) -> list[str]:
        return [key(c) for c in self.calls]


def by_name(results, name):
    return next(r for r in results if r.name == name)


def test_the_steps_run_in_the_loops_order_and_the_write_is_the_fourth():
    """§0's order, pinned: the tree, the config, the slot, THEN the one write, then the
    advisories. A test that only checked the verdict would let the write drift ahead."""
    r = Runner()
    results = mod.preflight(runner=r, cwd="/repo")
    assert r.keys() == ["git status", "check-project-config.sh", "tk.sh slot-check", "tk.sh autosync", "check-ports.sh", "df"]
    assert r.calls[1][-1] == "--strict", "exit 3 only exists under --strict"
    assert r.calls[3][1:] == ["autosync", "off"]
    assert [x.status for x in results] == [OK, OK, OK, OK, OK, INFO]
    assert mod.exit_code(results) == 0
    assert "READY" in mod.summary(results)
    assert set(r.cwds) == {"/repo"}, "every step runs in the project, not wherever the wrapper cd'd"


def test_a_dirty_tree_fails_names_the_files_and_the_write_does_not_happen():
    """A campaign on a dirty tree loses work, and the loop stops there — before
    `autosync off`. A pre-flight that fails must leave the tracker as it found it."""
    r = Runner(git_status=(0, " M src/app.py\n?? notes.md\n", ""))
    results = mod.preflight(runner=r, cwd="/repo")
    tree = by_name(results, "git status --porcelain")
    assert tree.status == FAIL and "src/app.py" in tree.detail and "notes.md" in tree.detail
    assert "2 path(s) dirty" in tree.detail
    assert by_name(results, "tk.sh autosync off").status == SKIP
    assert "tk.sh autosync" not in r.keys(), "the write ran on a failed gate"
    assert mod.exit_code(results) == 1
    # The other reads still run — the report is the reason to ask, and they change nothing.
    assert "check-ports.sh" in r.keys() and "df" in r.keys()


def test_a_long_dirty_list_is_capped_not_forwarded():
    paths = "".join(f"?? file{i}.py\n" for i in range(40))
    tree = mod.preflight(runner=Runner(git_status=(0, paths, "")), cwd="/repo")[0]
    assert "40 path(s) dirty" in tree.detail and "file39.py" not in tree.detail and "28 more" in tree.detail


def test_exit_3_from_the_config_check_propagates_as_the_upgrade_stop():
    """Exit 3 is the stop that needs the owner, in both modes. It must survive the
    collapse as exit 3, not be flattened into a generic 1."""
    r = Runner(**{"check-project-config.sh": UPGRADE})
    results = mod.preflight(runner=r, cwd="/repo")
    cfg = by_name(results, "check-project-config.sh --strict")
    assert cfg.status == UPGR and "0.9.4" in cfg.detail and "/harness-setup" in cfg.detail
    assert mod.exit_code(results) == EXIT_UPGRADE == 3
    assert by_name(results, "tk.sh autosync off").status == SKIP
    assert "run /harness-setup (exit 3)" in mod.summary(results)


def test_upgrade_wins_over_a_dirty_tree_because_setup_must_run_first():
    r = Runner(git_status=(0, " M a\n", ""), **{"check-project-config.sh": UPGRADE})
    assert mod.exit_code(mod.preflight(runner=r, cwd="/repo")) == 3


def test_a_config_that_fails_outright_is_a_plain_failure():
    r = Runner(**{"check-project-config.sh": (1, "", "FAIL:\n  - stack 'python-uv' names no marker\n")})
    results = mod.preflight(runner=r, cwd="/repo")
    cfg = by_name(results, "check-project-config.sh --strict")
    assert cfg.status == FAIL and "names no marker" in cfg.detail
    assert mod.exit_code(results) == 1


def test_a_free_slot_passes_and_a_held_one_names_its_holder_and_the_way_out():
    free = by_name(mod.preflight(runner=Runner(), cwd="/repo"), "tk.sh slot-check")
    assert free.status == OK and free.detail == "free"

    r = Runner(**{"tk.sh slot-check": (0, HELD + "\n", "")})
    held = by_name(mod.preflight(runner=r, cwd="/repo"), "tk.sh slot-check")
    assert held.status == FAIL and "campaign" in held.detail and "STALE" in held.detail
    assert "slot-release --force" in held.detail
    assert "tk.sh autosync" not in r.keys()

    alive = json.dumps({"free": False, "holder": "campaign-2", "stale": False})
    r = Runner(**{"tk.sh slot-check": (0, alive, "")})
    live = by_name(mod.preflight(runner=r, cwd="/repo"), "tk.sh slot-check")
    assert live.status == FAIL and "alive" in live.detail and "/halt" in live.detail


def test_a_slot_check_that_cannot_answer_is_a_failure_not_a_free_slot():
    """"Must exist" — a tracker that cannot be reached must not read as a free slot."""
    r = Runner(**{"tk.sh slot-check": (1, "", "no beads database found\n")})
    slot = by_name(mod.preflight(runner=r, cwd="/repo"), "tk.sh slot-check")
    assert slot.status == FAIL and "no beads database" in slot.detail
    r = Runner(**{"tk.sh slot-check": (0, "not json at all\n", "")})
    assert by_name(mod.preflight(runner=r, cwd="/repo"), "tk.sh slot-check").status == FAIL


def test_a_hang_is_a_failure_not_a_pass():
    """The recorded 8.5-hour stall was a command blocking on a prompt nobody saw."""
    r = Runner(**{"check-ports.sh": subprocess.TimeoutExpired(cmd="check-ports.sh", timeout=120)})
    ports = by_name(mod.preflight(runner=r, cwd="/repo"), "check-ports.sh")
    assert ports.status == FAIL and "no output within" in ports.detail


def test_a_bound_port_is_surfaced_on_the_line_even_though_the_check_is_advisory():
    """`check-ports.sh` without --strict exits 0 with a bound port and says so on
    stderr. The orchestrator must see that line, not a bare [ok]."""
    r = Runner(**{"check-ports.sh": (0, "project: T\nports free: api=8000\n",
                                     "BOUND: web=3000 is listening — pid(s) 4242\nA port bound before the run starts is a server left over…\n")})
    ports = by_name(mod.preflight(runner=r, cwd="/repo"), "check-ports.sh")
    assert ports.status == OK and "BOUND: web=3000" in ports.detail and "left over" not in ports.detail


def test_disk_headroom_is_informational_and_never_fails_the_run():
    r = Runner(df=(1, "", "df: .: Operation not permitted\n"))
    results = mod.preflight(runner=r, cwd="/repo")
    disk = by_name(results, "df -h .")
    assert disk.status == INFO and "Operation not permitted" in disk.detail
    assert mod.exit_code(results) == 0
    ok = by_name(mod.preflight(runner=Runner(), cwd="/repo"), "df -h .")
    assert ok.detail == "/dev/disk3  926Gi  650Gi  276Gi  71%", "the last line, the one with the numbers"


def test_the_memories_index_is_not_part_of_the_preflight():
    """That is content the loop reads separately; a gate that also prints it would put
    it in the orchestrator's context twice."""
    assert all("memories" not in " ".join(s.argv) for s in mod.STEPS)


def test_main_prints_one_line_per_step_a_summary_and_returns_the_exit_code(monkeypatch, capsys):
    r = Runner(**{"tk.sh slot-check": (0, HELD, "")})
    monkeypatch.setattr("models.steps.subprocess.run", r)
    assert mod.main([]) == 1
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.startswith("[")]
    assert [ln[:6] for ln in lines] == ["[ok  ]", "[ok  ]", "[FAIL]", "[skip]", "[ok  ]", "[info]"]
    assert "pre-flight: 3 ok, 1 failed, 0 upgrade, 1 skipped. BLOCKED" in out

    monkeypatch.setattr("models.steps.subprocess.run", Runner())
    assert mod.main([]) == 0
    assert "READY" in capsys.readouterr().out


def test_the_wrapper_is_executable_and_runs_the_module():
    sh = Path(mod.HARNESS) / "swarm" / "preflight.sh"
    assert sh.exists() and os.access(sh, os.X_OK), "a wrapper nobody can execute is prose"
    text = sh.read_text()
    assert "MAD_HARNESS_CALLER_PWD" in text and "python -m models.preflight" in text


@pytest.mark.parametrize("step", mod.STEPS)
def test_every_step_names_an_executable_that_ships(step):
    """A step naming a wrapper that moved fails every pre-flight — the exact failure
    `check-script-refs.sh` guards prose against, guarded here for code."""
    exe = step.argv[0]
    if "/" in exe:
        assert Path(exe).exists() and os.access(exe, os.X_OK), exe
