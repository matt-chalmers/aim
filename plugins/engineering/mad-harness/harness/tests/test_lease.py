"""Epic leases across machines.

Against a REAL remote and two real checkouts, because the property under test belongs to
the remote's ref update rather than to git — the plan that proposed this flagged exactly
that, and a mocked `git push` would only prove the mock agrees with my assumptions.
"""

from __future__ import annotations

import subprocess

import pytest

from tracker import lease as L


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


@pytest.fixture
def two_machines(tmp_path):
    """A bare remote and two independent checkouts of it."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)

    a = tmp_path / "a"
    a.mkdir()
    _git(["init", "-q", "."], a)
    _git(["commit", "-q", "--allow-empty", "-m", "base"], a)
    _git(["remote", "add", "origin", str(remote)], a)
    _git(["push", "-q", "origin", "HEAD:refs/heads/main"], a)

    b = tmp_path / "b"
    subprocess.run(["git", "clone", "-q", str(remote), str(b)], check=True)
    _git(["fetch", "-q", "origin", "main"], b)
    _git(["reset", "-q", "--hard", "origin/main"], b)
    return str(a), str(b)


def test_exactly_one_machine_wins_a_contested_epic(two_machines):
    """The whole point. Two campaigns on one epic would both claim tasks, both merge, and
    the tracked export would conflict on push or take the last write silently."""
    a, b = two_machines
    assert L.acquire("epic-1", holder="campaign-a", cwd=a) is not None
    assert L.acquire("epic-1", holder="campaign-b", cwd=b) is None, "two holders"

    still = L.inspect("epic-1", cwd=b)
    assert still is not None and still.holder == "campaign-a"


def test_different_epics_run_concurrently(two_machines):
    """Leasing at EPIC granularity rather than repository granularity is the point — it
    matches `campaign-loop`'s one-epic-at-a-time execution model and lets two machines work
    different epics."""
    a, b = two_machines
    assert L.acquire("epic-1", holder="campaign-a", cwd=a) is not None
    assert L.acquire("epic-2", holder="campaign-b", cwd=b) is not None
    assert sorted(L.held(cwd=a)) == ["epic-1", "epic-2"]


def test_the_holder_is_readable_from_the_other_machine(two_machines):
    """A refusal that cannot say who holds the epic sends someone to the wrong machine."""
    a, b = two_machines
    L.acquire("epic-1", holder="campaign-a", cwd=a)
    info = L.inspect("epic-1", cwd=b)
    assert info is not None
    assert info.holder == "campaign-a"
    assert info.host and info.pid
    assert "campaign-a" in info.describe()


def test_a_fresh_lease_is_never_stolen(two_machines):
    """A slow epic must not be taken from a live campaign."""
    a, b = two_machines
    L.acquire("epic-1", holder="campaign-a", cwd=a)
    assert L.steal("epic-1", holder="campaign-b", cwd=b) is None
    assert L.inspect("epic-1", cwd=a).holder == "campaign-a"


def test_a_stale_lease_is_reclaimable(two_machines):
    """Otherwise a crashed machine parks an epic until someone notices by hand."""
    a, b = two_machines
    L.acquire("epic-1", holder="campaign-a", cwd=a)
    assert L.steal("epic-1", ttl=0, holder="campaign-b", cwd=b) is not None
    assert L.inspect("epic-1", cwd=a).holder == "campaign-b"


def test_release_is_idempotent(two_machines):
    a, b = two_machines
    L.acquire("epic-1", holder="campaign-a", cwd=a)
    assert L.release("epic-1", cwd=a)
    assert L.release("epic-1", cwd=a), "releasing an unheld lease is success, not an error"
    assert L.held(cwd=a) == {}


def test_a_lease_is_never_pushed_without_an_object(tmp_path):
    """AN EMPTY SOURCE REFSPEC IS A DELETE.

    Measured while building this: a failed `commit-tree` produced
    `git push origin :refs/harness/epic-lease/<epic>`, which removed ANOTHER machine's
    lease. Acquiring must raise rather than push an empty source.
    """
    lonely = tmp_path / "not-a-repo"
    lonely.mkdir()
    with pytest.raises(L.LeaseError):
        L.acquire("epic-1", cwd=str(lonely))


@pytest.mark.parametrize("bad", ["", "   ", None, "a/b", "..", "epic-1 "])
def test_no_verb_builds_a_refspec_from_a_bad_epic_name(bad, monkeypatch):
    """`release` pushes `:refs/harness/epic-lease/<epic>` — a delete. With an empty
    epic that is a delete of the namespace root. `acquire` guarded its OBJECT; nothing
    guarded the NAME, for any verb. Now every refspec goes through one gate, and git is
    never reached: the fake below would fail the test if it were."""
    monkeypatch.setattr(L, "_git", lambda *a, **k: pytest.fail("git was invoked"))
    for verb in (L.acquire, L.release, L.steal):
        with pytest.raises(L.LeaseError):
            verb(bad)  # type: ignore[arg-type]


def test_a_good_epic_name_passes_the_gate():
    assert L._ref("TD-m7j7") == f"{L.NAMESPACE}/TD-m7j7"


def test_the_cli_runs_every_lease_verb_in_the_project_not_the_plugin(monkeypatch, tmp_path):
    """The wrapper `cd`s into the harness before Python starts, so git without a cwd ran
    in the plugin's own checkout — no `origin`, every verb threw "cannot reach the
    remote", and installed as a plugin NO EPIC WAS EVER LEASED. A second machine saw a
    clear field. The CLI must pass the resolved project to every call."""
    from tracker import cli

    monkeypatch.setattr("models.resolve.REPO", tmp_path)
    seen: list[tuple[str, str | None]] = []

    def spy(name, result):
        def f(*args, cwd=None, **kw):
            seen.append((name, cwd))
            return result
        return f

    monkeypatch.setattr(L, "held", spy("held", {}))
    monkeypatch.setattr(L, "inspect", spy("inspect", None))
    monkeypatch.setattr(L, "acquire", spy("acquire", L.Lease("e", "h", "host", 1, 0.0)))
    monkeypatch.setattr(L, "steal", spy("steal", L.Lease("e", "h", "host", 1, 0.0)))
    monkeypatch.setattr(L, "release", spy("release", True))

    for argv in (
        ["lease", "list"],
        ["lease", "show", "e"],
        ["lease", "acquire", "e"],
        ["lease", "steal", "e"],
        ["lease", "release", "e"],
    ):
        cli.main(argv)

    assert seen, "nothing was called"
    wrong = [(n, c) for n, c in seen if c != str(tmp_path)]
    assert wrong == [], f"lease verbs that did not run in the project: {wrong}"
    assert {n for n, _ in seen} == {"held", "inspect", "acquire", "steal", "release"}
