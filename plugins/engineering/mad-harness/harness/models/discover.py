"""What toolchains and frameworks are in play in this repository — an ENUMERATION, not a
second detector.

WHY THIS EXISTS. Until 0.13.0 detection was verification of a declaration: `Stack.present()`
and `Stack.ambiguous()` ran only over stacks `harness.yaml` already listed, and the module
directories were enumerated solely to populate an error message. A repository that gained a
`package-lock.json` after setup was never told `node-npm` existed, so `/harness-setup` could
not revisit anything. This adds the loop over modules × candidate roots; the classification
is the existing one, called on a module relocated to each root, because a second predicate
would be two truths about one fact.

FRAMEWORKS ARE ONLY EVER `candidate`. A framework module has no markers by design
(`frameworks/_template.yaml`: a `detect` key was removed for being inert). The hint is the
framework's name as a direct dependency in a manifest the project wrote — reading a
declaration, the same class as `check_commands._json_scripts`. It is a hint and no more: a
module named for its framework (`nextjs`) whose package has another name (`next`) is not
matched, which is why setup's agent also reads `dependencies()` itself.

WHAT CODE DOES NOT DECIDE. A toolchain or framework that NO module covers is named by setup's
agent, from `listing()` and `dependencies()` — a list of toolchain markers here would be the
built-in name list this repository rejects everywhere else (`check_commands` states why).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from . import project as _project
from .project import FRAMEWORKS_DIR, STACKS_DIR, Project, ProjectError, _load_stack
from .setup_blocks import stack_key

#: Conventional OUTPUT directories — build products and vendored trees, never where a
#: project keeps its own toolchain. Not toolchain names: a repository whose real toolchain
#: lives in one of these declares it by hand, and discovery only ever warns.
SKIP = frozenset(
    {"node_modules", "vendor", "dist", "build", "target", "__pycache__", "out"}
)
#: Roots probed beyond ".". Tens of `stat` calls per module; the cap keeps a sprawling
#: repository from making every config check slow, and is reported when it bites.
ROOT_CAP = 40


@dataclass(frozen=True)
class Finding:
    kind: str  # stack | framework
    name: str
    root: str  # "." or a depth-1 directory
    state: str  # present | ambiguous | candidate
    evidence: tuple[str, ...]
    declared: bool
    declined: bool
    reason: str = ""

    @property
    def key(self) -> str:
        return f"{self.name}@{self.root}"


def _repo() -> Path:
    # Read at call time, not import time: tests point the harness at a fixture repo by
    # monkeypatching `models.project.REPO`, which `Stack.present` already reads.
    return _project.REPO


def _ignored(paths: list[str], repo: Path) -> set[str]:
    """Which of these repo-relative paths git ignores — one call for the batch."""
    if not paths:
        return set()
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "check-ignore", "--stdin"],
            input="\n".join(paths),
            capture_output=True,
            text=True,
        )
    except OSError:
        return set()
    # Exit 1 means "none ignored"; 128 means not a git repository — nothing to subtract.
    return {ln.strip() for ln in out.stdout.splitlines() if ln.strip()}


def roots(repo: Path | None = None) -> tuple[tuple[str, ...], bool]:
    """`.` plus every depth-1 directory a toolchain could plausibly live in, and whether the
    cap cut the list. A dot-directory, an ignored one, or a conventional output directory is
    not a candidate."""
    repo = repo or _repo()
    dirs = sorted(
        p.name
        for p in repo.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name not in SKIP
    )
    ignored = _ignored(dirs, repo)
    kept = [d for d in dirs if d not in ignored]
    return (".", *kept[:ROOT_CAP]), len(kept) > ROOT_CAP


def listing(root: str, repo: Path | None = None) -> list[str]:
    """The root's top-level entries that git does not ignore — what setup's agent reads to
    name a toolchain no module covers. Directories carry a trailing `/`."""
    repo = repo or _repo()
    base = repo if root == "." else repo / root
    if not base.is_dir():
        return []
    names = sorted(
        p.name + ("/" if p.is_dir() else "") for p in base.iterdir() if p.name != ".git"
    )
    rel = [n.rstrip("/") if root == "." else f"{root}/{n.rstrip('/')}" for n in names]
    ignored = _ignored(rel, repo)
    return [n for n, r in zip(names, rel) if r not in ignored]


def _pep508_name(spec: str) -> str:
    """`Django>=4.2` / `uvicorn[standard]` / `foo ; python_version<'3.12'` → the bare name,
    normalised as PEP 503 does: Python package names are case- and separator-insensitive,
    so `Django` and `django` are one dependency, not a new one."""
    out = []
    for ch in spec.strip():
        if ch.isalnum() or ch in "-_.":
            out.append(ch)
        else:
            break
    return re.sub(r"[-_.]+", "-", "".join(out)).lower()


def dependencies(root: str, repo: Path | None = None) -> dict[str, list[str]]:
    """Direct dependency names per manifest under `root`, as the project declared them.

    One reader for three consumers: framework candidates, the snapshot setup records when
    block 3 is confirmed, and `check_project`'s new-dependency warning. Names only — never a
    version, never a value — and only from manifests the harness can parse; another
    ecosystem's dependencies are setup's agent's to read."""
    repo = repo or _repo()
    base = repo if root == "." else repo / root
    out: dict[str, list[str]] = {}

    pkg = base / "package.json"
    if pkg.is_file():
        try:
            d = json.loads(pkg.read_text())
            names = set((d.get("dependencies") or {})) | set(
                (d.get("devDependencies") or {})
            )
            out["package.json"] = sorted(names)
        except (OSError, ValueError, AttributeError):
            pass

    pyproject = base / "pyproject.toml"
    if pyproject.is_file():
        try:
            d = tomllib.loads(pyproject.read_text())
            names = {
                _pep508_name(s)
                for s in (d.get("project") or {}).get("dependencies") or []
            }
            poetry = ((d.get("tool") or {}).get("poetry") or {}).get(
                "dependencies"
            ) or {}
            names |= {_pep508_name(n) for n in poetry if n != "python"}
            out["pyproject.toml"] = sorted(n for n in names if n)
        except (OSError, tomllib.TOMLDecodeError, AttributeError):
            pass
    return out


def available(kind: str) -> dict[str, Path]:
    """Every module a project could adopt — shipped and project-local, project winning. A
    leading underscore is a template; `internal: true` marks the harness's own toolchain
    (`python-uv-selftest`), which no consumer should be offered. Declarative rather than a
    name rule, which is the name-list shape this corpus rejects."""
    if kind == "stack":
        dirs = (STACKS_DIR, _project.PROJECT_STACKS_DIR)
    else:
        dirs = (FRAMEWORKS_DIR, _project.PROJECT_FRAMEWORKS_DIR)
    found: dict[str, Path] = {}
    for d in dirs:  # project-local second, so it shadows
        if d.is_dir():
            for p in sorted(d.glob("*.yaml")):
                if not p.stem.startswith("_"):
                    found[p.stem] = p
    out = {}
    for name, path in sorted(found.items()):
        try:
            if (yaml.safe_load(path.read_text()) or {}).get("internal"):
                continue
        except (OSError, yaml.YAMLError):
            continue
        out[name] = path
    return out


def _declared(project: Project | None) -> tuple[set[str], set[str]]:
    if project is None:
        return set(), set()
    stacks = {stack_key(e) for e in (project.raw.get("stacks") or [])}
    frameworks = {f"{n}@." for n in (project.raw.get("frameworks") or [])}
    return stacks, frameworks


def discover(project: Project | None = None, repo: Path | None = None) -> list[Finding]:
    """Every available module classified at every candidate root.

    Works with no `harness.yaml`: on a first run there is no Project, every module is
    undeclared, and classification is marker-only. Absent (module, root) pairs are not
    findings."""
    repo = repo or _repo()
    declined = (
        project.declined() if project is not None else {"stacks": {}, "frameworks": {}}
    )
    dec_stacks, dec_frameworks = _declared(project)
    candidate_roots, _ = roots(repo)
    out: list[Finding] = []

    for name in available("stack"):
        for root in candidate_roots:
            try:
                s = _load_stack({"name": name, "root": root})
            except ProjectError:
                continue
            if s.present():
                state, evidence = (
                    "present",
                    tuple(s.at(m) for m in s.detect_any if (repo / s.at(m)).exists()),
                )
            elif s.ambiguous():
                state, evidence = (
                    "ambiguous",
                    tuple(
                        s.at(m) for m in s.detect_language if (repo / s.at(m)).exists()
                    ),
                )
            else:
                continue
            key = f"{name}@{root}"
            out.append(
                Finding(
                    "stack",
                    name,
                    root,
                    state,
                    evidence,
                    key in dec_stacks,
                    key in declined["stacks"],
                    declined["stacks"].get(key, ""),
                )
            )

    for name in available("framework"):
        for root in candidate_roots:
            hits = [
                f"{root}/{m}" if root != "." else m
                for m, names in dependencies(root, repo).items()
                if name in names
            ]
            if not hits:
                continue
            key = f"{name}@{root}"
            # A framework is declared project-wide, not per root.
            out.append(
                Finding(
                    "framework",
                    name,
                    root,
                    "candidate",
                    tuple(hits),
                    f"{name}@." in dec_frameworks,
                    key in declined["frameworks"],
                    declined["frameworks"].get(key, ""),
                )
            )
    return out


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        p = _project.load()
    except ProjectError:
        p = None  # first run: discovery must work before any config exists
    found = discover(p)
    candidate_roots, truncated = roots()
    if "--json" in args:
        print(
            json.dumps(
                {
                    "findings": [asdict(f) | {"key": f.key} for f in found],
                    "roots": list(candidate_roots),
                    "truncated": truncated,
                    "listing": {r: listing(r) for r in candidate_roots},
                    "dependencies": {
                        r: d for r in candidate_roots if (d := dependencies(r))
                    },
                },
                indent=2,
            )
        )
        return 0
    for f in found:
        mark = (
            "declared" if f.declared else ("DECLINED" if f.declined else "undeclared")
        )
        print(
            f"  {f.kind:<9} {f.name:<20} {f.root:<12} {f.state:<10} {mark:<10} ({', '.join(f.evidence)})"
        )
    if truncated:
        print(
            f"  … more than {ROOT_CAP} candidate roots; only the first {ROOT_CAP} were probed"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
