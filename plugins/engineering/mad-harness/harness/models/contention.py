"""File contention between the tasks of a wave — the layer `tk.sh validate` cannot see.

`graph.validate` levels an epic into waves by dependency alone, and `port.Validation`
says of its number "it ignores file contention" as a stated property: contention needs
git and the filesystem, which the port must not touch. `planner.md` calls the matrix "your
most important output" and had the planner compute it by eye — extract every path each
task names, grep the codebase, build the task × path matrix per wave, edge where a path
appears in two tasks of one wave — and the recorded pile-up is an epic rated 11-wide with
five wave-1 tasks touching one ~1,900-line module. This is that computation, for the
planner to RESOLVE (merge, serialise, split) rather than to derive.
"""

from __future__ import annotations

from pathlib import Path

from .resolve import REPO
from .wave_plan import line_count, paths_in


def contention_by_wave(store, validation, *, cwd: Path | None = None, megafile: int | None = None) -> dict:
    cwd = cwd or REPO
    if megafile is None:
        try:
            from .project import load

            megafile = load().megafile_lines()
        except Exception:  # noqa: BLE001 — no config, no megafile rule; said in the output
            megafile = None
    waves = []
    for w in validation.waves:
        named: dict[str, tuple[list[str], list[str]]] = {}
        for tid in w.task_ids:
            t = store.show(tid)
            text = "\n".join(x for x in ((t.description if t else ""), (t.acceptance if t else ""), (t.notes if t else "")) if x)
            named[tid] = paths_in(text, cwd)
        edges = []
        ids = list(w.task_ids)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                shared = sorted(set(named[a][0]) & set(named[b][0]))
                for p in shared:
                    n = line_count(cwd, p)
                    edges.append({"tasks": [a, b], "path": p, "megafile": bool(megafile and n > megafile), "lines": n})
                created = sorted(set(named[a][1]) & set(named[b][1]))
                for p in created:
                    edges.append({"tasks": [a, b], "path": p, "megafile": False, "lines": 0, "both_create": True})
        waves.append({"index": w.index, "tasks": ids, "edges": edges})
    return {"megafile_lines": megafile, "waves": waves, "note": "dependency waves ignore file contention; every edge here is a pair that cannot share a wave as planned"}
