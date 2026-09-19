"""Point the harness at itself for its own tests.

The config loader, the worker bootstrap and the area map are all functions of a
project, so testing them needs one. The plugin uses itself — a real config
exercising the real schema, rather than a synthetic fixture free to drift from it.
"""

from __future__ import annotations

import os
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("MAD_HARNESS_REPO", str(PLUGIN_ROOT))


def pytest_sessionstart(session):
    """THE SUITE RUNS FROM A CHECKOUT, AND SAYS SO. Its corpus sweeps enumerate the shipped
    files with `git ls-files` — exactly what ships, never scratch — and refuse to pass on
    zero files; its revision readers exercise `peek.sh` and `brief.py` against HEAD. An
    installed plugin cache is not a git repository, and a consumer who found `make
    harness-test` in the setup skill and ran the suite from the cache got fifteen failures
    that said nothing about their configuration. One line here instead."""
    import subprocess

    import pytest

    probe = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=str(PLUGIN_ROOT), capture_output=True, text=True,
    )
    if probe.returncode != 0:
        pytest.exit(
            f"the harness's own suite runs from the plugin's source checkout; {PLUGIN_ROOT} is not "
            f"inside a git repository (an installed plugin cache is not one). A consumer's "
            f"verification is `make project`, `make skills`, `make models`, `make commands` and the "
            f"worktree probe — harness-setup §7.",
            returncode=4,
        )
