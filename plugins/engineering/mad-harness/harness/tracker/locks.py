"""The worker mutexes. ONE implementation, whatever backend holds the records.

NAMED `locks` RATHER THAN `coordination` DELIBERATELY. `tracker/__init__.py` exposes a
`coordination()` factory, and a submodule of the same name shadows it the moment either
is imported — `tracker.coordination` then resolves to the module and calling it raises
`TypeError: module object is not callable`. The factory names are the public surface, so
the modules yield. Same reason `events.py` is not `telemetry.py`.

WHY THIS IS NOT PART OF THE BACKEND. Every wave runs N worktrees on ONE machine against
ONE filesystem — `worker.py` already points every worktree at the primary checkout. So
claiming a task and holding the merge slot coordinate *processes*, not storage, and a
per-backend implementation would be the same code written twice with two chances to get
it subtly wrong.

WHAT IS ACTUALLY BEING GUARDED, WHICH IS SMALLER THAN IT LOOKS. The orchestrator assigns
a specific id and the worker is told never to pick a different one, so in normal operation
there is exactly ONE contender per task. The claim is a guard against ORCHESTRATOR ERROR —
a double-dispatch, or a stale re-dispatch after `/halt` — not an allocator. It still has
to be correct, because a wrong guard corrupts a wave silently; it does not have to be fast
under contention, because there is none.

THE PRIMITIVES, AND WHY EACH IS THE RIGHT ONE.

    claim        O_CREAT|O_EXCL. Atomic create is the only compare-and-set POSIX offers
                 without a daemon, and it is exactly the semantics wanted: one winner,
                 everyone else learns who won.
    slot lease   the same create, PLUS liveness. Acquire and release are separate CLI
                 invocations, so no held file lock can span them — a lease with a pid to
                 probe is what survives the process boundary.
    writes       write-temp + os.replace. A reader never sees a half-written file.

A DEAD HOLDER IS STOLEN, AND THE STEAL IS RECORDED. Today a worker that dies holding the
merge slot strands it until somebody runs `merge-slot release --holder <name>` by hand.
Probing the recorded pid removes that step — but only when the lease was taken on THIS
host, because a pid from another machine means nothing here. That check is also the
cross-machine detector: a lease from another host is reported, never stolen.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from .port import ClaimResult, SlotState, TrackerError

#: Where the run-state lives. Set by `.swarm-env` for a worker, exactly as `BEADS_DB` is,
#: so every worktree coordinates through the PRIMARY checkout rather than its own tree.
RUN_DIR_ENV = "HARNESS_RUN_DIR"

#: How old a lease must be before another holder may take it. THE ONLY staleness signal,
#: because the acquiring process exits before the holder is finished with the lease.
#: Deliberately generous: a wave gate legitimately holds the slot for minutes, and stealing
#: a live holder's slot is far worse than waiting out a dead one. `/halt` is the fast path
#: for a genuinely stranded slot, and `slot_release --force` is its instrument.
STALE_AFTER_S = 3600


def _primary_checkout(start: Path | None = None) -> Path:
    """The primary checkout, from anywhere inside the repo or one of its worktrees.

    `git worktree list --porcelain` lists the primary first — the same derivation
    `models/worker.py` uses to resolve the shared task database.
    """
    cwd = str(start or Path.cwd())
    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if top.returncode != 0:
        raise TrackerError(f"{cwd} is not inside a git repository")
    listing = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=top.stdout.strip(),
        capture_output=True,
        text=True,
    ).stdout
    for line in listing.splitlines():
        if line.startswith("worktree "):
            return Path(line.split(" ", 1)[1])
    return Path(top.stdout.strip())


def run_dir(start: Path | None = None) -> Path:
    """The coordination root, honouring the env the worker bootstrap exports.

    THE REPO IS THE ONE THE HARNESS OPERATES ON, not whichever directory the process
    happens to sit in. `models/resolve` has always honoured `MAD_HARNESS_REPO`; this did
    not, so the two disagreed the moment cwd differed from the target — and dispatch
    telemetry for waves run against another repository was filed into the harness's own
    `.harness/run/`, leaving the repository that did the work with an empty cost series
    and `models/report.py` reading the wrong one.

    `HARNESS_RUN_DIR` still wins: a worker bootstrap points it at the primary checkout
    explicitly, and that is more specific than either.
    """
    override = os.environ.get(RUN_DIR_ENV)
    if override:
        return Path(override)
    target = os.environ.get("MAD_HARNESS_REPO")
    if target:
        return Path(target).resolve() / ".harness" / "run"
    return _primary_checkout(start) / ".harness" / "run"


def _now() -> float:
    return time.time()


def _identity(actor: str) -> dict:
    return {
        "actor": actor,
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "at": _now(),
    }


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _create_exclusive(path: Path, body: dict) -> bool:
    """Atomically create `path` WITH its content. False means somebody else won.

    WRITE-THEN-LINK, NOT CREATE-THEN-WRITE, and the difference is a race that actually
    fired. `O_CREAT|O_EXCL` is atomic about the file's EXISTENCE but not its CONTENT:
    between the create and the write there is a window in which the file is zero bytes.
    A concurrent contender that read it there got empty JSON, and the caller — correctly
    refusing to treat an unreadable claim as free — raised. Eight contenders raced under
    load and ALL EIGHT failed, so a task nobody held looked claimed by somebody broken.

    `os.link` closes the window: the content is complete in the temporary file before the
    name exists at all, and linking onto an existing name fails rather than clobbering.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".mk-")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(body, fh)
        try:
            os.link(tmp, path)
        except FileExistsError:
            return False
        return True
    finally:
        os.unlink(tmp)


def _write_atomic(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    with os.fdopen(fd, "w") as fh:
        json.dump(body, fh)
    os.replace(tmp, path)


def _definitely_alive(rec: dict) -> bool:
    """Whether the recorded pid is CERTAINLY still running on this host.

    ONE-DIRECTIONAL ON PURPOSE, and this cost a real race. The first version treated a
    dead pid as proof the lease was abandoned — but acquire and release are SEPARATE CLI
    invocations, so the process that took the lease has almost always exited by the time
    anyone else looks. Eight contenders raced and two won: the loser probed a pid that had
    already gone, declared the lease stale, and stole it. A slot everybody can steal is
    worse than no slot, because everybody believes it works.

    So a live pid proves NOT stale; a dead one proves nothing, and age decides. A pid from
    another host names a different process here, so it is never probed at all.
    """
    if rec.get("host") != socket.gethostname():
        return False
    pid = rec.get("pid")
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by another user
    return True


class FileCoordination:
    """Claims and the merge slot, as files under the primary checkout's run directory."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root else run_dir()

    # --- claims ---------------------------------------------------------------

    def _claim_path(self, task_id: str) -> Path:
        safe = task_id.replace("/", "-")
        return self.root / "claims" / f"{safe}.claim"

    def try_claim(self, task_id: str, actor: str) -> ClaimResult:
        path = self._claim_path(task_id)
        if _create_exclusive(path, _identity(actor)):
            return ClaimResult(held=True, holder=actor)
        rec = _read(path)
        if rec is None:
            # Present but unreadable. Refuse rather than assume: a corrupt claim that
            # reads as "free" is exactly the double-dispatch this exists to prevent.
            raise TrackerError(
                f"claim file {path} exists but cannot be read; resolve it by hand"
            )
        holder = str(rec.get("actor") or "?")
        if holder == actor:
            return ClaimResult(held=True, holder=actor, reentrant=True)
        return ClaimResult(held=False, holder=holder)

    def release_claim(self, task_id: str, actor: str) -> bool:
        path = self._claim_path(task_id)
        rec = _read(path)
        if rec is None:
            return False
        if str(rec.get("actor")) != actor:
            return False
        path.unlink(missing_ok=True)
        return True

    def claim_holder(self, task_id: str) -> dict | None:
        """The full claim record, for the cross-host warning at pre-flight."""
        return _read(self._claim_path(task_id))

    def foreign_claims(self) -> list[dict]:
        """Claims recorded by another host — the cheap cross-machine collision detector.

        It cannot prevent two machines claiming the same task; it turns a silent
        corruption into something pre-flight can print.
        """
        me = socket.gethostname()
        out = []
        for p in sorted((self.root / "claims").glob("*.claim")):
            rec = _read(p)
            if rec and rec.get("host") and rec["host"] != me:
                out.append({**rec, "task": p.stem})
        return out

    # --- the merge slot -------------------------------------------------------

    @property
    def _slot(self) -> Path:
        return self.root / "slot.lock"

    def slot_check(self) -> SlotState:
        rec = _read(self._slot)
        if rec is None:
            return SlotState(free=True)
        holder = str(rec.get("actor") or "?")
        # AGE decides staleness. A live pid can only ever veto it — see _definitely_alive
        # for the race that taught this.
        aged = (_now() - float(rec.get("at") or 0)) > STALE_AFTER_S
        return SlotState(free=False, holder=holder, stale=aged and not _definitely_alive(rec))

    def slot_acquire(self, holder: str) -> bool:
        if _create_exclusive(self._slot, _identity(holder)):
            return True
        rec = _read(self._slot)
        if rec is None:
            raise TrackerError(f"{self._slot} exists but cannot be read")
        if str(rec.get("actor")) == holder:
            return True  # idempotent for one holder
        state = self.slot_check()
        if state.stale:
            # Steal, and RECORD it. A silent steal is how two workers end up committing
            # at once believing each holds the mutex.
            _write_atomic(self._slot, {**_identity(holder), "stole_from": state.holder})
            return True
        return False

    def slot_release(self, holder: str, *, force: bool = False) -> bool:
        rec = _read(self._slot)
        if rec is None:
            return False
        if not force and str(rec.get("actor")) != holder:
            return False
        self._slot.unlink(missing_ok=True)
        return True
