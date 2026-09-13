"""What the harness needs to know about the repository it is running in.

THE SEAM THAT MAKES THE HARNESS REUSABLE. Everything the harness does splits in
two: machinery that is true anywhere (routing a model, batching a search,
summarising a diff) and facts that are true only here (where the docs live, what
counts as a security surface, how to make a fresh worktree usable). The machinery
is code in `harness/`; the facts are `harness/harness.yaml` and the stack modules
under `harness/stacks/`. A different project supplies different YAML and the code
is unchanged.

The split between the two YAML layers is deliberate and worth keeping:

    harness.yaml    DECLARATIVE facts about this repository — paths, the area
                    map, the security surface. Answers "what is true here".
    stacks/*.yaml   PROCEDURAL knowledge about a toolchain — how to restore
                    dependencies, what a parallel worker must export, how to run
                    the suite. Answers "what does using uv/npm mean".

A project names the stacks it uses; the stack module says what using one means.
Supporting a new toolchain is a new file under `stacks/`, not a change in here —
that is the extension point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .resolve import HARNESS, REPO

#: The consuming repository's own config. It lives in THAT repo, not beside the
#: harness code — the harness is shared, the config is not.
PROJECT_FILE = REPO / "harness.yaml"

#: Stack modules ship with the harness. A project may add its own alongside; both
#: directories are searched, project-local winning, so supporting a new toolchain
#: never requires editing the installed plugin.
STACKS_DIR = HARNESS / "stacks"
PROJECT_STACKS_DIR = REPO / ".harness" / "stacks"

#: Framework modules — "how do I WRITE good code here", as opposed to a stack's
#: "how do I RUN things here". The axes are independent: one toolchain serves many
#: frameworks, and one framework runs on many toolchains. Conflating them is what
#: makes a harness unable to serve Python+FastAPI after being taught Python+Django.
FRAMEWORKS_DIR = HARNESS / "frameworks"
PROJECT_FRAMEWORKS_DIR = REPO / ".harness" / "frameworks"


class ProjectError(RuntimeError):
    """The project or stack config is wrong in a way that must stop the harness."""


@dataclass(frozen=True)
class Area:
    path: str
    label: str
    triggers: tuple[str, ...] = ()


#: Grants the OPERATOR has approved, read from `harness.yaml`.
#:
#: An agent may request one by filing a `permission` record; only a person may write one
#: here, which `check_commands._NORMATIVE` enforces — an agent able to edit this key could
#: approve its own request. Kept in the project config rather than a side file so a grant
#: is reviewable in a diff and travels with the repository instead of varying per machine,
#: which is the failure `--setting-sources project` exists to prevent.
def granted_permissions(config: dict) -> tuple[str, ...]:
    block = config.get("permissions") or {}
    return tuple(str(g) for g in (block.get("allow") or ()))


@dataclass(frozen=True)
class Stack:
    """A toolchain, and WHERE in this repository it lives.

    `root` is the seam. Everything else a stack knows — its markers, its dependency
    directory, the directory its commands run in — is relative to it, so relocating a
    toolchain is one field rather than four kept in agreement by hand. The module
    ships with `root: "."` because a root layout is the common case; a project that
    keeps its toolchain in a subdirectory says so in its own config, since where a
    toolchain lives is a fact about the project rather than about the toolchain.
    """

    name: str
    description: str
    dependency_dir: str
    bootstrap: dict[str, Any]
    env: dict[str, str]
    commands: dict[str, str]
    #: Markers specific to THIS TOOLCHAIN — a lockfile, not a manifest. Any one is
    #: enough; they are alternatives, not requirements.
    detect_any: tuple[str, ...] = ()
    #: Markers that identify only the LANGUAGE. Matching one of these and none of
    #: `detect_any` is ambiguous, not absent: the repo is Python, but perhaps Poetry's.
    detect_language: tuple[str, ...] = ()
    root: str = "."
    banned_forms: tuple[dict[str, str], ...] = ()
    #: Where this toolchain should keep its cache, as `ENV_VAR: repo-relative path`.
    #:
    #: A sandbox confines a worker to its checkout, which is correct until `uv` cannot
    #: reach `~/.cache/uv` and every command dies with "Failed to initialize cache ...
    #: Operation not permitted". The obvious fix — granting `~/.cache/uv` — was measured
    #: and DOES NOT WORK: the failure is EPERM on a file inside the granted directory,
    #: not a plain write refusal. Relocating the cache INSIDE the boundary does work, and
    #: is better anyway: it pokes no hole into the home directory. The path resolves
    #: against the primary checkout so every worktree shares one cache.
    cache_env: dict[str, str] = field(default_factory=dict)
    #: The module's own YAML, so the card can be read without reparsing.
    raw: dict = field(default_factory=dict)

    @property
    def card_raw(self) -> str:
        return (self.raw.get("card") or "").strip()

    def at(self, rel: str | None) -> str:
        """A stack-relative path, as the repository sees it."""
        parts = [p for p in (self.root, rel or ".") if p and p != "."]
        return "/".join(parts) if parts else "."

    @property
    def deps_path(self) -> str:
        return self.at(self.dependency_dir)

    @property
    def bootstrap_cwd(self) -> str:
        return self.at((self.bootstrap or {}).get("cwd"))

    @property
    def commands_cwd(self) -> str:
        return self.at((self.commands or {}).get("cwd"))

    def present(self) -> bool:
        """Whether the TOOLCHAIN's own marker is here.

        Deliberately not satisfied by a language marker. `pyproject.toml` is shared by
        uv, Poetry, PDM, Hatch and plain pip, so treating it as proof of uv lets a
        Poetry repo pass config validation and fail later inside a worker's worktree,
        mid-wave — the expensive place, and one the escalation policy would misread as
        the worker's fault rather than the config's.
        """
        return any((REPO / self.at(p)).exists() for p in self.detect_any)

    def ambiguous(self) -> bool:
        """The language is here but the toolchain's own marker is not."""
        return not self.present() and any(
            (REPO / self.at(p)).exists() for p in self.detect_language
        )

    def worker_env(self, slug: str, worker: int) -> dict[str, str]:
        """This stack's env for one worker, with {slug} and {worker} substituted."""
        return {k: v.format(slug=slug, worker=worker) for k, v in self.env.items()}


@dataclass(frozen=True)
class Project:
    name: str
    slug: str
    stacks: tuple[Stack, ...]
    paths: dict[str, str]
    areas: tuple[Area, ...]
    security: dict[str, Any]
    raw: dict[str, Any] = field(default_factory=dict)

    def area_for(self, changed_path: str) -> Area | None:
        for a in self.areas:
            if changed_path.startswith(a.path):
                return a
        return None

    def triggers_for(self, changed_paths: list[str]) -> set[str]:
        """Which verification lenses a change must fire, from the area map alone.

        Path-based only. A task's SURFACE: line can fire a lens the paths do not,
        and that is deliberate — a zero path grep is not an exemption.
        """
        out: set[str] = set()
        for p in changed_paths:
            a = self.area_for(p)
            if a:
                out.update(a.triggers)
        return out

    def framework_configs(self) -> list[dict]:
        """Raw config for each framework this project declares."""
        out = []
        for name in self.raw.get("frameworks") or []:
            local = PROJECT_FRAMEWORKS_DIR / f"{name}.yaml"
            path = local if local.exists() else FRAMEWORKS_DIR / f"{name}.yaml"
            try:
                d = yaml.safe_load(path.read_text()) or {}
            except FileNotFoundError as exc:
                available = sorted(
                    p.stem
                    for p in FRAMEWORKS_DIR.glob("*.yaml")
                    if not p.stem.startswith("_")
                )
                raise ProjectError(
                    f"harness.yaml names framework {name!r}, which has no {path}. "
                    f"Available: {available or '<none>'}. Adding one is a new file there, "
                    f"never a code change."
                ) from exc
            if d.get("name") != name:
                raise ProjectError(
                    f"framework file {path.name} declares name={d.get('name')!r}; the "
                    f"filename is what harness.yaml resolves against, so the two must agree"
                )
            out.append(d)
        return out

    def role(self, name: str) -> str | None:
        """The path this project uses for a named structural role.

        Agents must never name a directory. `backend/api` is one project's spelling
        of "the API surface"; another has `src/routes` or no such thing at all. The
        agent asks for the ROLE and the project supplies the path, so a prompt that
        says "the API surface" stays true everywhere.

        Returns None when the project has no such role — which is a real answer, not
        an error. A repository with no separate services layer should not be told to
        invent one.
        """
        return ((self.raw.get("layout") or {}).get("roles") or {}).get(name)

    def roles(self) -> dict[str, str]:
        return dict((self.raw.get("layout") or {}).get("roles") or {})

    #: Backends the harness ships. A project naming anything else is a config error, not
    #: a runtime one — the alternative is a wave that dispatches and then discovers
    #: mid-flight that its tracker does not exist.
    TRACKER_BACKENDS = ("beads", "mdfiles")

    def tracker(self) -> dict[str, Any]:
        """The `tracker:` block, validated. Absent means tasks, exactly as before.

        Defaulting rather than requiring the block is what makes this layer invisible to
        every project that had one before it existed.
        """
        cfg = dict(self.raw.get("tracker") or {})
        backend = str(cfg.get("backend") or "beads")
        if backend not in self.TRACKER_BACKENDS:
            raise ProjectError(
                f"tracker.backend is {backend!r}; known backends are "
                f"{', '.join(self.TRACKER_BACKENDS)}. Adding one is a new module under "
                f"harness/tracker/, named here."
            )
        if backend == "mdfiles" and not cfg.get("export"):
            raise ProjectError(
                "tracker.backend is 'mdfiles' but no tracker.export is declared. The hot "
                "store is gitignored, so without an export path nothing the workers write "
                "is ever committed — the backlog would live only on the machine that ran "
                "the wave."
            )
        limits = cfg.get("limits") or {}
        if "record_bytes" in limits and limits["record_bytes"] is not None:
            try:
                int(limits["record_bytes"])
            except (TypeError, ValueError) as exc:
                raise ProjectError(
                    f"tracker.limits.record_bytes must be a number or null, got "
                    f"{limits['record_bytes']!r}"
                ) from exc
        cfg["backend"] = backend
        return cfg

    def archive_dir(self) -> str | None:
        """Where spent epic staging folders go, or None to delete them as before.

        THE LOCATION IS THE WHOLE SAFETY ARGUMENT, so it is asserted rather than trusted.
        Four sweeps glob the staging root or the docs tree, and an archive inside either
        breaks them — most sharply `check-decision-register.sh`, whose contention pass
        would treat every retired proposal as a live contender and report `lands_in`
        clashes forever. An archive under an `areas` path would likewise feed
        `check-doc-drift.sh` an ever-growing set of dead hits, and a check that cries wolf
        is one people learn to ignore.

        Opt-in: absent means today's behaviour, so no existing config changes meaning.
        """
        raw = (self.raw.get("paths") or {}).get("archive")
        if not raw:
            return None
        archive = str(raw).strip("/")
        proposed = str((self.paths or {}).get("proposed") or "").strip("/")
        docs = str((self.paths or {}).get("docs") or "").strip("/")

        def nests(a: str, b: str) -> bool:
            return (
                bool(a)
                and bool(b)
                and (a == b or a.startswith(b + "/") or b.startswith(a + "/"))
            )

        for name, other in (("paths.proposed", proposed), ("paths.docs", docs)):
            if nests(archive, other):
                raise ProjectError(
                    f"paths.archive ({archive!r}) overlaps {name} ({other!r}). The archive "
                    f"must sit outside both, or the sweeps that read them treat retired "
                    f"proposals as live ones — the decision register's contention pass "
                    f"would clash on every archived epic, forever."
                )
        for area in self.areas:
            if nests(archive, str(area.path).strip("/")):
                raise ProjectError(
                    f"paths.archive ({archive!r}) overlaps the area {area.path!r}. "
                    f"check-doc-drift.sh sweeps every area, and an archive there adds a "
                    f"permanently growing set of dead hits."
                )
        return archive

    def domain_nouns(self) -> list[str]:
        """Every word that means something in this product and nothing in general software.

        DERIVED, NOT MAINTAINED. A hand-written list was the first attempt and it does
        not work: a product has hundreds of domain terms, nobody can predict which one
        will bleed into a prompt, and the list will not be updated when the product
        grows. Measured on a real project — a curated list of 20 against 103 the corpus
        already names, and it omitted the single word that had leaked twice.

        So the terms come from where the product already writes them down: the glossary's
        headings, the entity index, and the feature folder names. Regenerated every run,
        so coverage tracks the docs at no maintenance cost.

        `domain.ambiguous` subtracts words that also mean something in software
        generally — DECLARED rather than inferred from what currently appears in the
        harness, because inferring it would exempt exactly the words that have already
        leaked. `domain.nouns` adds anything the docs do not name.
        """
        import re

        d = self.raw.get("domain") or {}
        terms: set[str] = {str(n).lower() for n in (d.get("nouns") or [])}

        for rel in d.get("derive_from") or []:
            path = REPO / rel
            if path.is_dir():
                terms |= {
                    p.name.replace("-", " ").lower()
                    for p in path.iterdir()
                    if p.is_dir()
                }
            elif path.is_file():
                text = path.read_text(errors="ignore")
                terms |= {
                    m.strip().lower() for m in re.findall(r"^#{2,3} (.+)$", text, re.M)
                }
                terms |= {
                    m.lower() for m in re.findall(r"^\| `([a-z_]{4,})`", text, re.M)
                }

        ambiguous = {str(a).lower() for a in (d.get("ambiguous") or [])}
        return sorted(
            t for t in terms if t and not t.startswith("_") and t not in ambiguous
        )

    def bead_prefix(self) -> str:
        """The issue-id prefix this repository's tasks use.

        A check that hard-codes this cannot fail in a foreign repo — it reports
        clean because none of its patterns matched, which is indistinguishable
        from having found nothing wrong.
        """
        prefix = (self.raw.get("beads") or {}).get("prefix")
        if not prefix:
            raise ProjectError(
                "harness.yaml declares no beads.prefix. The task-hygiene checks build "
                "their patterns from it; without it they would match nothing and report "
                "clean, which is worse than not running them."
            )
        return str(prefix)

    def security_paths(self) -> list[str]:
        return list(self.security.get("paths") or [])

    def security_tokens(self) -> list[str]:
        return list(self.security.get("tokens") or [])

    def invariants(self) -> list[str]:
        return list(self.security.get("invariants") or [])

    def worker_env(self, worker: int) -> dict[str, str]:
        env: dict[str, str] = {}
        for s in self.stacks:
            env.update(s.worker_env(self.slug, worker))
        return env


def _stack_path(name: str) -> Path:
    """Project-local stack modules shadow the harness's own."""
    local = PROJECT_STACKS_DIR / f"{name}.yaml"
    return local if local.exists() else STACKS_DIR / f"{name}.yaml"


def _available_stacks() -> list[str]:
    names = {p.stem for p in STACKS_DIR.glob("*.yaml")}
    if PROJECT_STACKS_DIR.exists():
        names |= {p.stem for p in PROJECT_STACKS_DIR.glob("*.yaml")}
    return sorted(names)


def _load_stack(entry: str | dict) -> Stack:
    """Load a stack module, with the location the PROJECT supplies.

    `harness.yaml` may name a stack as a bare string (root layout, no ceremony) or as
    `{name: node-npm, root: web}` when the toolchain lives in a subdirectory. The
    module itself never names a location — that is the project's fact, and hard-coding
    it into the shipped module is what made these usable only in a monorepo.
    """
    if isinstance(entry, dict):
        name = entry.get("name")
        if not name:
            raise ProjectError(f"stack entry {entry!r} has no `name`")
        overrides = {k: v for k, v in entry.items() if k != "name"}
    else:
        name, overrides = entry, {}

    path = _stack_path(name)
    try:
        d = yaml.safe_load(path.read_text()) or {}
    except FileNotFoundError as exc:
        available = _available_stacks()
        raise ProjectError(
            f"harness.yaml names stack {name!r}, which has no {path}. "
            f"Available: {available or '<none>'}. Adding a toolchain is a new file there."
        ) from exc
    for key in ("name", "dependency_dir", "commands"):
        if key not in d:
            raise ProjectError(f"stack {name!r} is missing {key!r}")
    if d["name"] != name:
        raise ProjectError(
            f"stack file {path.name} declares name={d['name']!r}; the filename is what "
            f"harness.yaml resolves against, so the two must agree"
        )
    if not (d.get("detect_any") or d.get("detect")):
        raise ProjectError(
            f"stack {name!r} declares no `detect_any` — without a toolchain marker "
            f"nothing can tell whether it is actually in use"
        )
    # `commands` MERGES rather than replaces. A project overriding one rotted
    # command must not silently drop the other five the module declares — and a
    # repair written by check_commands.py is exactly a one-key override.
    merged_commands = {
        **(d.get("commands") or {}),
        **(overrides.pop("commands", None) or {}),
    }
    d = {**d, **overrides, "commands": merged_commands}
    return Stack(
        name=d["name"],
        description=d.get("description", ""),
        # `detect` is the pre-root spelling; accept it so an existing module keeps working.
        detect_any=tuple(d.get("detect_any") or d.get("detect") or ()),
        detect_language=tuple(d.get("detect_language") or ()),
        root=str(d.get("root") or "."),
        dependency_dir=d["dependency_dir"],
        bootstrap=d.get("bootstrap") or {},
        env=d.get("env") or {},
        commands=d.get("commands") or {},
        banned_forms=tuple(d.get("banned_forms") or ()),
        cache_env=dict(d.get("cache_env") or {}),
        raw=d,
    )


def load(path: Path | None = None) -> Project:
    path = path or PROJECT_FILE
    try:
        d = yaml.safe_load(path.read_text()) or {}
    except FileNotFoundError as exc:
        raise ProjectError(
            f"no project config at {path}. The harness needs one to know where this "
            f"repository keeps its docs, what its areas are, and what its security "
            f"surface is — see harness/README.md."
        ) from exc
    except yaml.YAMLError as exc:
        raise ProjectError(f"{path} is not valid YAML: {exc}") from exc

    for key in ("name", "slug", "areas"):
        if key not in d:
            raise ProjectError(f"{path} is missing {key!r}")

    areas = tuple(
        Area(path=a["path"], label=a["label"], triggers=tuple(a.get("triggers") or ()))
        for a in d["areas"]
    )
    stacks = tuple(_load_stack(n) for n in (d.get("stacks") or []))
    return Project(
        name=d["name"],
        slug=d["slug"],
        stacks=stacks,
        paths=d.get("paths") or {},
        areas=areas,
        security=d.get("security") or {},
        raw=d,
    )
