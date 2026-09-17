"""campaign-loop §3a's reuse / delta / rebuild decision, mechanically — and its baseline.

The spec index records the SHA it was generated against and every doc it cites. Whether
it is still good is therefore a diff, not a judgement: nothing it cites moved since that
SHA → REUSE; some did → DELTA-survey those; no index → REBUILD with a full SURVEY.

Two ways this answered wrongly, both plausibly rather than loudly:

* The staging folder is `<bare-id>-<slug>`, the glob used the id exactly as given, and
  every other command takes the PREFIXED id. So `PROJ-m7j7` matched nothing and printed
  `REBUILD — no spec index` — a legitimate verdict for an epic that has none — and the loop
  dispatched a ~120k-token survey to rebuild an index already on disk. Either id form is
  accepted now, and a folder that is not found says so and names the patterns tried.
* A DELTA survey verified the changed docs, wrote its findings to the epic, and left the
  index's `generated_sha` where it was. The next run diffed against the same old SHA,
  found the same paths moved, and dispatched the same survey — forever. `--stamp` moves
  the baseline to the HEAD that was verified; §3a runs it after a successful DELTA.
"""

from __future__ import annotations

import datetime as _dt
import re
import subprocess
import sys
from pathlib import Path

from .project import ProjectError, load
from .resolve import REPO

_FRONT = re.compile(r"^---\n(.*?)\n---\n", re.S)


def _proposed() -> Path:
    """The staging directory, from config. No default: a wrong guess globs a path that
    does not exist, finds nothing, and reports clean."""
    d = str((load().paths or {}).get("proposed") or "").strip()
    if not d:
        raise ProjectError("harness.yaml declares no paths.proposed — cannot locate staged files")
    return REPO / d


def _index_for(epic: str) -> tuple[Path | None, Path | None, list[str]]:
    """(folder, spec-index.md, patterns tried). Folder None = nothing staged for the epic;
    index None with a folder = staged, but never surveyed."""
    from tracker.staging import staged_folder

    folder, tried = staged_folder(epic, _proposed())
    if folder is None:
        return None, None, tried
    idx = folder / "spec-index.md"
    return folder, (idx if idx.is_file() else None), tried


def _frontmatter(path: Path) -> tuple[dict, str]:
    import yaml

    text = path.read_text(encoding="utf-8")
    m = _FRONT.match(text)
    if not m:
        raise ProjectError(f"{path} has no frontmatter — the citations and baseline live there")
    return (yaml.safe_load(m.group(1)) or {}), text[m.end():]


def status(epic: str) -> tuple[str, str]:
    """(verdict, report). Verdict is REUSE, DELTA or REBUILD."""
    folder, idx, tried = _index_for(epic)
    if folder is None:
        return "REBUILD", (
            f"REBUILD — no staging folder for {epic}. Tried: {', '.join(tried)}. "
            f"A full SURVEY creates one."
        )
    if idx is None:
        return "REBUILD", f"REBUILD — {folder} exists but holds no spec-index.md. Dispatch a full SURVEY."

    fm, _ = _frontmatter(idx)
    sha, cites = fm.get("generated_sha"), list(fm.get("cites") or [])
    if not sha or not cites:
        raise ProjectError(f"{idx} must record generated_sha and a non-empty cites list")
    missing = [c for c in cites if not (REPO / c).exists()]
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{sha}..HEAD", "--", *cites],
        cwd=str(REPO), capture_output=True, text=True, timeout=60,
    )
    if out.returncode:
        raise ProjectError(f"git diff failed against {sha} — is that SHA in this history?\n{out.stderr.strip()}")
    moved = [x for x in out.stdout.split() if x]

    lines = [
        f"index: {idx.relative_to(REPO)}",
        f"baseline: {sha}  ·  cites {len(cites)} docs  ·  verdict recorded: {fm.get('verdict', '?')}",
    ]
    if missing:
        lines.append("  ! cited doc no longer exists: " + ", ".join(missing))
    if moved or missing:
        lines += ["", "DELTA — hand analyst-survey this index plus the changed paths, and ask it to verify and extend:"]
        lines += [f"  {x}" for x in sorted(set(moved) | set(missing))]
        lines += ["", f"When that survey lands, move the baseline: spec-index-status.sh {epic} --stamp"]
        verdict = "DELTA"
    else:
        lines += ["", "REUSE — nothing it cites has moved. Do not re-dispatch; say so in the report with the index date."]
        verdict = "REUSE"
    lines += ["", "Note: the VERDICT is stale regardless if the epic's children changed — adequacy is judged",
              "against what is being built, not only against the corpus."]
    return verdict, "\n".join(lines)


def stamp(epic: str, cites: list[str] | None = None) -> str:
    """Move the index's baseline to HEAD — `generated_sha`, `generated_at` — and extend
    `cites` with any paths the survey added. The frontmatter is rewritten in place; the
    body is untouched. Returns the new sha."""
    _, idx, tried = _index_for(epic)
    if idx is None:
        raise ProjectError(f"no spec-index.md to stamp for {epic}. Tried: {', '.join(tried)}")
    head = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"], cwd=str(REPO), capture_output=True, text=True, timeout=30
    ).stdout.strip()
    if not head:
        raise ProjectError(f"cannot resolve HEAD in {REPO}")
    fm, body = _frontmatter(idx)
    existing = list(fm.get("cites") or [])
    fm["cites"] = existing + [c for c in (cites or []) if c not in existing]
    fm["generated_sha"] = head
    fm["generated_at"] = _dt.date.today().isoformat()
    import yaml

    idx.write_text("---\n" + yaml.safe_dump(fm, sort_keys=False).rstrip() + "\n---\n" + body, encoding="utf-8")
    return head


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0].startswith("-"):
        print("usage: spec-index-status.sh <epic-id> [--stamp [--cite <path>]...]", file=sys.stderr)
        return 2
    epic = args[0]
    try:
        if "--stamp" in args:
            cites = [args[i + 1] for i, a in enumerate(args) if a == "--cite" and i + 1 < len(args)]
            head = stamp(epic, cites)
            print(f"stamped {epic}: baseline {head}, {_dt.date.today().isoformat()}"
                  + (f", +{len(cites)} cite(s)" if cites else ""))
            return 0
        verdict, report = status(epic)
        print(report)
        return 0
    except ProjectError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
