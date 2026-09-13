"""Task-tracker abstraction: records, coordination, telemetry and memory.

The factories here are the only place a backend is chosen. Everything else — the checks,
the dispatcher, the lens brief — asks for a port and never learns which implementation it
got, which is what makes the backend swappable at all.

SELECTION IS OPT-IN AND DEFAULTS TO TODAY'S BEHAVIOUR. A project with no `tracker:` block
gets tasks, exactly as before, so adding this layer changes nothing until somebody asks
it to.
"""

from __future__ import annotations

from typing import Any

from .port import (  # noqa: F401 — re-exported as the public surface
    BLOCKED,
    CLOSED,
    DECISION,
    EPIC,
    GATE,
    IN_PROGRESS,
    OPEN,
    OPEN_STATUSES,
    TASK,
    Capabilities,
    ClaimResult,
    Coordination,
    Event,
    MemoryStore,
    NotSupported,
    SlotState,
    Task,
    TaskStore,
    Telemetry,
    TrackerError,
    Validation,
    Wave,
)

DEFAULT_BACKEND = "beads"


def _config() -> dict[str, Any]:
    """The `tracker:` block, or an empty one. A missing project config is not fatal here.

    Several callers run outside a configured repository (the tests, `--help`), and a
    tracker that refuses to construct would turn those into crashes for no gain.
    """
    try:
        from models.project import load

        return dict(load().raw.get("tracker") or {})
    except Exception:  # noqa: BLE001 — an unconfigured project still gets the default
        return {}


def _repo_path(value):
    """A config path, resolved against the REPOSITORY rather than the cwd.

    Config paths are written relative to the repo that declares them, but the wrappers
    `cd` into the harness before running Python — so a bare `docs/tasks` resolved to a
    directory inside the installed plugin. The export then succeeded, wrote real files,
    and put them somewhere the consuming project would never look: the failure is silent
    and it looks like it worked.
    """
    if not value:
        return None
    from pathlib import Path

    from models.resolve import REPO

    p = Path(value)
    return p if p.is_absolute() else REPO / p


def backend_name() -> str:
    return str(_config().get("backend") or DEFAULT_BACKEND)


def task_store(name: str | None = None) -> TaskStore:
    chosen = name or backend_name()
    if chosen == "beads":
        from models.resolve import REPO

        from .beads import BeadsTaskStore

        # `cwd` IS REQUIRED, not a nicety. `bd` resolves its workspace from the working
        # directory, and every wrapper cd's into the harness before running Python — so a
        # store built without one asked the PLUGIN's directory for the consuming repo's
        # records, and got "no beads database found". The conformance suite passes cwd
        # explicitly, so nothing caught it until a real repository was pointed at.
        return BeadsTaskStore(cwd=str(REPO))
    if chosen == "mdfiles":
        from .mdfiles import MdTaskStore

        cfg = _config()
        return MdTaskStore(
            root=_repo_path(cfg.get("dir")),
            export_dir=_repo_path(cfg.get("export")),
        )
    raise TrackerError(
        f"unknown tracker backend {chosen!r}. Known: tasks, mdfiles. "
        f"Adding one is a new module under harness/tracker/, named in harness.yaml."
    )


def memory_store(name: str | None = None) -> MemoryStore:
    chosen = name or backend_name()
    if chosen == "beads":
        from models.resolve import REPO

        from .beads import BeadsMemoryStore

        return BeadsMemoryStore(cwd=str(REPO))
    if chosen == "mdfiles":
        from .mdfiles import MdMemoryStore

        return MdMemoryStore(_repo_path(_config().get("dir")))
    raise TrackerError(f"unknown tracker backend {chosen!r}")


def coordination() -> Coordination:
    """The worker mutexes. One implementation, never backend-specific."""
    from .locks import FileCoordination

    return FileCoordination()


def telemetry() -> Telemetry:
    """The cost series, with the legacy bridge wired while tasks is still in play.

    The bridge is what makes moving telemetry cost no history: old `event` tasks are
    merged on read rather than imported, so there is no migration step to half-finish.
    """
    from .events import LocalTelemetry

    legacy = None
    if backend_name() == "beads":
        from models.resolve import REPO

        from .beads import legacy_events

        legacy = lambda: legacy_events(cwd=str(REPO))  # noqa: E731
    return LocalTelemetry(legacy_reader=legacy)
