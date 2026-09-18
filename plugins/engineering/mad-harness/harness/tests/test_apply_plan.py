"""One call applies a planner's plan — campaign-loop §3e + §3f without a model in the loop.

Measured: the orchestrator's context averaged ~380k tokens across 237 requests, so each of
the 10-30 tool calls §3e cost per epic re-read that at ~$0.11-0.17 a call, about 6x a
worker's price, to do something with no judgement in it. These tests drive the real
wrapper against a real markdown tracker in a scratch repository — the plan is text an
agent wrote, and the failure that matters is one the CLI would reject mid-plan.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from models import apply_plan as mod
from models.resolve import PLUGIN_ROOT

TK = PLUGIN_ROOT / "harness" / "tracker" / "tk.sh"
ROOT = "${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh"


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    """A consuming repository on the markdown backend, and the harness pointed at it —
    for this process and for every tk.sh it spawns."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "harness.yaml").write_text(
        "name: Trial\nslug: trial\nstacks: []\nareas: []\npaths: {proposed: docs/proposed}\n"
        "tracker: {backend: mdfiles, dir: .harness/tasks, export: docs/tasks}\n"
    )
    monkeypatch.setenv("MAD_HARNESS_REPO", str(repo))
    monkeypatch.setenv("MAD_HARNESS_CALLER_PWD", str(repo))
    monkeypatch.delenv("HARNESS_RUN_DIR", raising=False)
    return repo


def _tk(*argv: str) -> str:
    proc = subprocess.run([str(TK), *argv], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _show(task_id: str) -> dict:
    return json.loads(_tk("show", task_id, "--json"))[0]


def _plan(repo: Path, epic: str, body: str, name: str = "plan.md") -> Path:
    text = (
        "# Plan\n\n1. The DAG\n\n```\nT1 Add the model\n  T2 Expose it (after T1)\n```\n\n"
        "4. Commands\n\n```bash\n" + body.replace("EPIC", epic) + "```\n"
    )
    (repo / name).write_text(text)
    return repo / name


THREE = f"""\
T1: {ROOT} create "Add the model" --parent EPIC \\
  --description "Model, migration and service."
{ROOT} update T1 --acceptance "a migration exists and is reversible"
T2: {ROOT} create "Expose the endpoint" --parent EPIC   # trailing comment
T3: /some/other/install/harness/tracker/tk.sh create "Regenerate client types" --parent EPIC
{ROOT} dep T2 T1
{ROOT} dep T3 T2
T4: {ROOT} create "A subtask of the model" --parent T1
"""


def test_a_labelled_plan_applies_and_every_record_and_edge_exists(repo, capsys):
    epic = _tk("create", "An epic", "-t", "epic")
    plan = _plan(repo, epic, THREE)
    view = repo / "docs" / "proposed" / f"{epic}-trial" / "tasks.md"

    assert mod.main([str(plan), "--epic", epic, "--render", str(view)]) == 0
    out = capsys.readouterr().out

    state = json.loads((repo / ".harness" / "run" / f"apply-plan-{epic}.json").read_text())
    ids = {k: v["id"] for k, v in state["labels"].items()}
    assert set(ids) == {"T1", "T2", "T3", "T4"}
    t1, t2, t3, t4 = (_show(ids[k]) for k in ("T1", "T2", "T3", "T4"))
    assert t1["title"] == "Add the model" and t1["parent"] == epic
    assert t1["description"] == "Model, migration and service."  # the continuation joined
    assert "reversible" in t1["acceptance"]
    assert t2["depends_on"] == [ids["T1"]] and t3["depends_on"] == [ids["T2"]]
    assert t4["parent"] == ids["T1"], "`--parent T1` resolves to the id T1's create printed"
    # One line per command, the id beside its label, and the summary the loop reads.
    assert f"T1 -> {ids['T1']}  created \"Add the model\"" in out
    assert f"dep {ids['T2']} -> {ids['T1']}" in out
    assert "applied 7 commands: 4 created, 0 already applied, 2 deps, 1 other" in out
    assert "3 waves" in out and "ignores file contention" in out
    assert view.is_file() and "Expose the endpoint" in view.read_text()


def test_a_dry_run_prints_every_command_with_labels_symbolic_and_writes_nothing(repo, capsys):
    epic = _tk("create", "An epic", "-t", "epic")
    plan = _plan(repo, epic, THREE)
    assert mod.main([str(plan), "--epic", epic, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "T1: tk.sh create 'Add the model' --parent " + epic in out
    assert "tk.sh dep T2 T1" in out and "T4: tk.sh create 'A subtask of the model' --parent T1" in out
    assert "dry-run: 7 commands, nothing written" in out
    assert _tk("list", "--parent", epic) == "", "a dry run created nothing"
    assert not (repo / ".harness" / "run").exists(), "a dry run recorded nothing"


@pytest.mark.parametrize(
    "bad, reason",
    [
        (f"{ROOT} dep T2 T9\n", "`T9` is not a label this plan creates"),
        (f"{ROOT} dep T9 T1\nT9: {ROOT} create \"Late\" --parent EPIC\n", "used before its create"),
        (f"T9: {ROOT} create --graph plan.json\n", "`create --graph`"),
        (f"{ROOT} prime\n", "`prime`"),
        (f"{ROOT} close T1 \"done\"\n", "positional `close <id> \"msg\"`"),
        ("cd /somewhere\n", "not a tk.sh command: `cd`"),
        (f"{ROOT} --readonly update T1 --title x\n", "`--readonly`"),
        (f"{ROOT} claim T1\n", "`claim` is not a plan verb"),
        (f"T9: {ROOT} dep T1 T2\n", "label `T9` on a `dep` line"),
        (f"{ROOT} update T1 --nonsense x\n", "does not parse"),
        (f"{ROOT} delete EPIC\n", "never delete the epic"),
        (f"{ROOT} update T1 --description \"never closed\n", "quotes never balance"),
    ],
)
def test_a_plan_with_one_bad_line_is_refused_whole_before_anything_is_written(repo, capsys, bad, reason):
    """Half a plan applied by a model is the state this replaces. Exit 2, the reason
    named, and a tracker that still holds only the epic."""
    epic = _tk("create", "An epic", "-t", "epic")
    plan = _plan(repo, epic, THREE + bad)
    assert mod.main([str(plan), "--epic", epic]) == 2
    err = capsys.readouterr().err
    assert "nothing written" in err and reason in err, err
    assert _tk("list", "--parent", epic) == "", "the good lines before the bad one must not have run"
    assert not (repo / ".harness" / "run").exists()


def test_an_unknown_epic_and_a_plan_without_a_bash_block_are_refused(repo, capsys):
    plan = _plan(repo, "t-000000", THREE)
    assert mod.main([str(plan), "--epic", "t-000000"]) == 2
    assert "epic t-000000 is not a record" in capsys.readouterr().err
    (repo / "none.md").write_text("# a plan\n\n```\ntk.sh create x\n```\n")
    assert mod.main([str(repo / "none.md"), "--epic", "t-000000"]) == 2
    assert "no tk.sh command in any ```bash block" in capsys.readouterr().err


def test_a_rerun_after_a_mid_plan_failure_skips_what_was_applied_and_finishes(repo, capsys, monkeypatch):
    """The label map is written after every create, so the retry resumes by label —
    not by line number, which the fix to the failing line has just shifted."""
    epic = _tk("create", "An epic", "-t", "epic")
    plan = _plan(repo, epic, THREE)

    real = mod.Tracker.run

    def failing(self, *argv):
        # The first edge is refused by the backend — after T1, T2 and T3 were created and
        # T1 was updated, before T4 exists.
        if argv[:1] == ("dep",) and failing.trip:
            failing.trip = False
            return subprocess.CompletedProcess(argv, 2, "", "FAIL: the backend refused this edge")
        return real(self, *argv)

    failing.trip = True
    monkeypatch.setattr(mod.Tracker, "run", failing)
    assert mod.main([str(plan), "--epic", epic]) == 1
    captured = capsys.readouterr()
    assert "FAILED at line" in captured.err and "the backend refused this edge" in captured.err
    assert "applied 4 of 7" in captured.err
    state = json.loads((repo / ".harness" / "run" / f"apply-plan-{epic}.json").read_text())
    assert set(state["labels"]) == {"T1", "T2", "T3"}, "every create before the failure is recorded"
    t1 = state["labels"]["T1"]["id"]

    monkeypatch.setattr(mod.Tracker, "run", real)
    # The retry: the plan is edited above the failing line, which shifts every line number.
    plan.write_text(plan.read_text().replace("# Plan", "# Plan\n\nA paragraph inserted on the retry.\n"))
    assert mod.main([str(plan), "--epic", epic]) == 0
    out = capsys.readouterr().out
    assert f"T1 = {t1}  (already created)" in out
    assert "update " + t1 + "  (already applied)" in out, "a line that ran once does not run twice"
    assert "applied 7 commands: 1 created, 4 already applied, 2 deps, 0 other" in out
    listed = _tk("list", "--parent", epic).splitlines()
    assert len(listed) == 3, f"no duplicate records on the retry: {listed}"
    ids = {k: v["id"] for k, v in json.loads(
        (repo / ".harness" / "run" / f"apply-plan-{epic}.json").read_text()
    )["labels"].items()}
    assert _show(ids["T3"])["depends_on"] == [ids["T2"]] and _show(ids["T2"])["depends_on"] == [t1]


def test_a_plan_that_validates_badly_is_applied_then_reported_as_not_ok(repo, capsys):
    """§3f in the same call. An edge to a task outside the epic is a dangling edge the
    DAG check reports — the records exist, the summary says so, and the exit is 1."""
    epic = _tk("create", "An epic", "-t", "epic")
    outside = _tk("create", "A task in some other epic")
    plan = _plan(repo, epic, f"T1: {ROOT} create \"Depends outward\" --parent EPIC\n{ROOT} dep T1 {outside}\n")
    assert mod.main([str(plan), "--epic", epic]) == 1
    captured = capsys.readouterr()
    assert "applied 2 commands: 1 created" in captured.out
    assert outside in captured.out and '"orphans"' in captured.out, "validate's own output is printed"
    assert f"validate {epic}: NOT OK" in captured.err
    assert len(_tk("list", "--parent", epic).splitlines()) == 1


def test_a_reused_label_with_a_different_title_is_a_collision_not_a_resume(repo, capsys):
    """A revised plan starts its labels at T1 again. Skipping its create because an
    earlier apply used the label would silently substitute the old task for the new one."""
    epic = _tk("create", "An epic", "-t", "epic")
    assert mod.main([str(_plan(repo, epic, THREE)), "--epic", epic]) == 0
    capsys.readouterr()
    revised = _plan(repo, epic, f"T1: {ROOT} create \"Something else entirely\" --parent EPIC\n"
                                f"{ROOT} create \"Unlabelled\" --parent EPIC\n", name="v2.md")
    assert mod.main([str(revised), "--epic", epic]) == 2
    err = capsys.readouterr().err
    assert "`T1` was created as" in err and "titled 'Add the model'" in err
    assert "an unlabelled create on a rerun" in err
    assert f"apply-plan-{epic}.json" in err, "the refusal names the file to delete to start over"


def test_a_relative_plan_path_resolves_where_the_caller_stood(repo, capsys, monkeypatch):
    """The wrapper `cd`s into the harness before Python starts; `plan.md` means the
    caller's `plan.md`, not the harness's."""
    epic = _tk("create", "An epic", "-t", "epic")
    _plan(repo, epic, THREE)
    monkeypatch.chdir(PLUGIN_ROOT / "harness")
    assert mod.main(["plan.md", "--epic", epic, "--dry-run"]) == 0
    assert "dry-run: 7 commands" in capsys.readouterr().out


def test_the_parser_reads_only_bash_fences_and_joins_what_a_planner_wraps():
    text = (
        "```\nT1: tk.sh create 'in a plain fence, so not a command'\n```\n"
        "```bash\n"
        "# a comment\n\n"
        "T1: tk.sh create \"Two\nlines\" --parent e\n"
        "tk.sh update T1 \\\n  --description 'continued'  # trailing\n"
        "tk.sh gate create T1 --reason because\n"
        "```\n"
    )
    commands, problems, labels = mod.parse(text)
    assert problems == [] and labels == {"T1"}
    assert [c.line for c in commands] == [7, 9, 11]
    assert commands[0].title == "Two\nlines" and commands[0].creates
    assert commands[1].argv == ("update", "T1", "--description", "continued")
    assert commands[2].creates and commands[2].refs == ("T1",)
    assert commands[1].resolved({"T1": "t-abc"}) == ["update", "t-abc", "--description", "continued"]


def test_the_wrapper_exists_is_executable_and_runs_this_module():
    sh = PLUGIN_ROOT / "harness" / "swarm" / "apply-plan.sh"
    assert sh.exists() and sh.stat().st_mode & 0o111
    text = sh.read_text()
    assert "MAD_HARNESS_CALLER_PWD" in text and "python -m models.apply_plan" in text


def test_the_planner_contract_states_the_label_form_the_applier_reads():
    """Two readers of one convention: the planner writes it, this module parses it. The
    contract lives in the planner's prompt; the parser's regex must match what it says."""
    text = (PLUGIN_ROOT / "agents" / "planner.md").read_text()
    contract = text.split("## Output contract")[1]
    assert "T1:" in contract and "apply-plan.sh" in contract
    assert "dep T2 T1" in contract or "dep T2 T1" in contract.replace("`", "")
    assert mod.LABEL.match("T1: ${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh create x").group("label") == "T1"
    assert mod.LABEL.match("data-model: tk.sh create x").group("label") == "data-model"
    assert mod.LABEL.match("1T: tk.sh create x") is None, "a label starts with a letter"
    assert mod.LABEL.match("${CLAUDE_PLUGIN_ROOT}/harness/tracker/tk.sh create x") is None
