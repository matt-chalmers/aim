"""Epic leases on a git ref, so two machines cannot work the same epic.

THE GAP THIS CLOSES. Claims and the merge slot live in `.harness/run/`, which is local to a
checkout — correct for the workers of one campaign, and no protection at all between two
machines. Both can claim the same task, both can merge, and the tracked export conflicts on
push or silently takes the last write. Nothing detects it.

WHY A GIT REF. The remote is the only thing two machines already share, so it needs no new
infrastructure and no server. Creating a ref that would not fast-forward is rejected by the
remote, which gives exactly one winner.

MEASURED AGAINST A REAL REMOTE before being relied on, because the atomicity belongs to the
remote's ref update rather than to git:

    A: push <object>:refs/harness/epic-lease/epic-1   -> [new reference], exit 0
    B: push <object>:refs/harness/epic-lease/epic-1   -> rejected, exit 1, holder unchanged
    A: push :refs/harness/epic-lease/epic-1           -> released, exit 0

EPIC GRANULARITY, not repository. `campaign-loop` already declares "THE LOOP IS SERIAL. ONE
EPIC AT A TIME", so a lease per epic matches the execution model and lets two machines work
different epics concurrently — which is more useful than a global lock and simpler than
per-task locking.

WHAT THIS DOES NOT SOLVE, stated rather than discovered later:
  * the tracked export still merges across machines — disjoint epics make conflicts rare,
    not impossible
  * cross-epic dependency edges go stale; pulling before composing a wave bounds it
  * nothing stops two campaigns in ONE checkout, which is what the dirty-tree check is for
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from dataclasses import dataclass

NAMESPACE = "refs/harness/epic-lease"

#: A lease older than this is reclaimable. Long enough that a slow epic is never stolen
#: from a live campaign; short enough that a crashed machine does not park an epic for a
#: working day.
DEFAULT_TTL_SECONDS = 6 * 60 * 60


class LeaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class Lease:
    epic: str
    holder: str
    host: str
    pid: int
    at: float
    sha: str = ""

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.at)

    def is_stale(self, ttl: int = DEFAULT_TTL_SECONDS) -> bool:
        return self.age_seconds > ttl

    def describe(self) -> str:
        mins = int(self.age_seconds // 60)
        return f"{self.epic} held by {self.holder} on {self.host} (pid {self.pid}), {mins}m ago"


def _git(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=cwd, timeout=60
    )


def _ref(epic: str) -> str:
    """The lease ref for `epic` — and the ONE place an epic name becomes a refspec.

    `release` pushes an empty source (`:refs/.../<epic>`), which is a delete. With an
    empty or whitespace epic that is `:refs/harness/epic-lease/` — a delete of the
    namespace root, or of whatever the remote resolves that to. `acquire` guards its
    object; this guards the name, for every verb.
    """
    name = (epic or "").strip()
    if not name or "/" in name or ".." in name or name != epic:
        raise LeaseError(f"invalid epic name for a lease: {epic!r}")
    return f"{NAMESPACE}/{name}"


def _identity(holder: str | None) -> dict:
    return {
        "holder": holder or os.environ.get("TRACKER_ACTOR") or os.environ.get("USER") or "unknown",
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "at": time.time(),
    }


def acquire(epic: str, *, holder: str | None = None, cwd: str | None = None) -> Lease | None:
    """Take the lease for `epic`, or return None if someone else holds it.

    The payload is a commit whose message carries the identity, so `ls-remote` plus one
    `cat-file` tells another machine who holds what without cloning anything.
    """
    ref = _ref(epic)  # validate the name before any git runs
    ident = _identity(holder)
    tree = _git(["rev-parse", "HEAD^{tree}"], cwd)
    if tree.returncode != 0 or not tree.stdout.strip():
        raise LeaseError("cannot resolve HEAD^{tree}; not a git repository with a commit")

    made = _git(
        ["commit-tree", "-m", f"epic-lease {epic}\n\n{json.dumps(ident)}", tree.stdout.strip()],
        cwd,
    )
    obj = made.stdout.strip()
    # AN EMPTY SOURCE REFSPEC IS A DELETE. Measured: a failed commit-tree produced
    # `push origin :refs/...`, which removed ANOTHER machine's lease. Never push without
    # an object.
    if made.returncode != 0 or not obj:
        raise LeaseError(f"could not build the lease object: {made.stderr.strip()[:160]}")

    pushed = _git(["push", "origin", f"{obj}:{ref}"], cwd)
    if pushed.returncode == 0:
        return Lease(epic=epic, sha=obj, **ident)
    return None


def release(epic: str, *, cwd: str | None = None) -> bool:
    """Give up the lease. Idempotent: releasing one nobody holds is success."""
    return _git(["push", "origin", f":{_ref(epic)}"], cwd).returncode == 0


def held(cwd: str | None = None) -> dict[str, str]:
    """Every epic currently leased, as `epic -> sha`. One network call."""
    listed = _git(["ls-remote", "origin", f"{NAMESPACE}/*"], cwd)
    if listed.returncode != 0:
        raise LeaseError(f"cannot reach the remote: {listed.stderr.strip()[:160]}")
    out: dict[str, str] = {}
    for line in listed.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].startswith(f"{NAMESPACE}/"):
            out[parts[1][len(NAMESPACE) + 1 :]] = parts[0]
    return out


def inspect(epic: str, *, cwd: str | None = None) -> Lease | None:
    """Who holds `epic`, by reading the lease object. None if unleased."""
    ref = _ref(epic)
    sha = held(cwd).get(epic)
    if not sha:
        return None
    _git(["fetch", "-q", "origin", ref], cwd)
    body = _git(["cat-file", "-p", sha], cwd)
    ident: dict = {}
    for line in body.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                ident = json.loads(line)
            except json.JSONDecodeError:
                pass
    return Lease(
        epic=epic,
        holder=str(ident.get("holder", "unknown")),
        host=str(ident.get("host", "unknown")),
        pid=int(ident.get("pid", 0)),
        at=float(ident.get("at", 0.0)),
        sha=sha,
    )


def steal(epic: str, *, ttl: int = DEFAULT_TTL_SECONDS, holder: str | None = None,
          cwd: str | None = None) -> Lease | None:
    """Reclaim a lease whose holder is gone. Refuses while it is still fresh.

    The steal is a ref update, so it is RECORDED rather than silent — the reflog on the
    remote shows what displaced what, which is the property the local merge-slot steal
    also has and for the same reason.
    """
    _ref(epic)  # validate before the first network call
    current = inspect(epic, cwd=cwd)
    if current is None:
        return acquire(epic, holder=holder, cwd=cwd)
    if not current.is_stale(ttl):
        return None
    if not release(epic, cwd=cwd):
        return None
    return acquire(epic, holder=holder, cwd=cwd)
