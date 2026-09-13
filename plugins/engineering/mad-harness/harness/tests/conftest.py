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
