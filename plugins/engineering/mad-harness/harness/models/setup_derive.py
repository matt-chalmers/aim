"""What the repository already says about each setup block — so nobody answers from scratch
what the repo states.

ONE FUNCTION PER BLOCK, each returning a `Draft`: the values it could derive (ready for the
writer), the evidence for each (a path, not an argument — short enough to sit in one
parenthetical), the candidates it found but will NOT write (shown with their source, for a
human to confirm), and what only the owner can answer.

THE LINE THE SPEC DRAWS: enumeration, classification and measurement are code; inference is
setup's agent's. So nothing here decides a security rule, names a toolchain no module covers,
or tells a framework from a library — it lists what is there and says where it came from.
A candidate is never a value: ports, security paths and framework hits are shown and
confirmed, because a grep that examined nothing while looking as if it passed is already on
record for `ports:`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import project as _project
from .discover import Finding, dependencies, discover, listing, roots
from .project import Project, ProjectError, _load_stack
from .resolve import PLUGIN_ROOT
from .setup_blocks import stack_key

TEMPLATE = PLUGIN_ROOT / "templates" / "harness.yaml.example"

#: The lines a consuming repository's `.gitignore` must carry before its first wave — the
#: hot store, the worker worktrees, per-worker scratch. Stated HERE, once: block 6 reports
#: which are missing and the skill shows that report rather than its own copy.
GITIGNORE_LINES = (
    ".claude/worktrees/",
    ".harness/cache/",
    ".harness/run/",
    ".harness/tasks/",
    ".swarm*",
)


@dataclass
class Draft:
    block: str
    #: Top-level key → value, ready for the writer. Owed keys are ABSENT, never guessed.
    values: dict[str, Any] = field(default_factory=dict)
    #: Dotted path → its source, short. Two richer entries feed the agent: `listing` and
    #: `dependencies` (per root), from which it names what no module covers.
    evidence: dict[str, Any] = field(default_factory=dict)
    #: Path → [{"value": …, "source": "file:line"}] — shown, never written.
    candidates: dict[str, list[dict]] = field(default_factory=dict)
    #: What only the owner can answer, one line each.
    owed: list[str] = field(default_factory=list)
    #: Whether an empty answer is a legitimate one here.
    declinable: bool = False


@dataclass
class Ctx:
    repo: Path
    raw: dict  # the current config, or {} on a first run
    project: Project | None
    findings: list[Finding]
    roots: tuple[str, ...]
    #: Injected into the command probes so tests need not run real toolchains.
    runner: Any = None
    #: The stacks decision blocks after 2 build on — the current config's, else derived.
    stacks: list[Any] | None = None


def context(runner=None) -> Ctx:
    repo = _project.REPO
    raw: dict = {}
    project = None
    if _project.PROJECT_FILE.exists():
        try:
            raw = yaml.safe_load(_project.PROJECT_FILE.read_text()) or {}
            project = _project.load()
        except (ProjectError, yaml.YAMLError):
            project = None
    candidate_roots, _ = roots(repo)
    return Ctx(repo, raw, project, discover(project, repo), candidate_roots, runner)


def _git(repo: Path, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True
        )
    except OSError:
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _template() -> dict:
    return yaml.safe_load(TEMPLATE.read_text()) or {}


# --- block 1: identity --------------------------------------------------------------------


def _name_sources(repo: Path) -> list[tuple[str, str]]:
    """Every name the repository already gives itself, best first, with where it said so."""
    out: list[tuple[str, str]] = []
    remote = _git(repo, "remote", "get-url", "origin")
    if remote:
        out.append(
            (
                re.sub(
                    r"\.git$",
                    "",
                    remote.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1],
                ),
                "git remote",
            )
        )
    pkg = repo / "package.json"
    if pkg.is_file():
        try:
            name = json.loads(pkg.read_text()).get("name")
            if name:
                out.append((str(name).rsplit("/", 1)[-1], "package.json"))
        except (ValueError, OSError, AttributeError):
            pass
    pyproject = repo / "pyproject.toml"
    if pyproject.is_file():
        try:
            name = (tomllib.loads(pyproject.read_text()).get("project") or {}).get(
                "name"
            )
            if name:
                out.append((str(name), "pyproject.toml"))
        except (tomllib.TOMLDecodeError, OSError):
            pass
    for readme in ("README.md", "README.rst", "README"):
        p = repo / readme
        if p.is_file():
            m = re.search(r"^#\s+(.+?)\s*#*\s*$", p.read_text(errors="ignore"), re.M)
            if m:
                out.append((m.group(1), readme))
            break
    out.append((repo.name, "directory name"))
    return out


def slug_of(name: str) -> str:
    """Lowercase letters and digits only — the slug interpolates into every per-worker
    variable (`{slug}_w3`), a database name among them."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def derive_identity(ctx: Ctx) -> Draft:
    d = Draft("identity")
    sources = _name_sources(ctx.repo)
    if ctx.raw.get("name"):
        sources.insert(0, (str(ctx.raw["name"]), "already declared"))
    name, source = sources[0]
    slug = str(ctx.raw.get("slug") or slug_of(name))
    d.values = {"name": name, "slug": slug}
    d.evidence = {
        "name": source,
        "slug": "already declared" if ctx.raw.get("slug") else f"from name {name!r}",
    }
    others = [{"value": n, "source": s} for n, s in sources[1:] if n != name]
    if others:
        d.candidates["name"] = others
    if not d.values["slug"]:
        del d.values["slug"]
        d.owed.append("a slug: the name has no letters or digits to build one from")
    return d


# --- block 2: stacks ----------------------------------------------------------------------


def _current_stacks(raw: dict) -> dict[str, Any]:
    return {stack_key(e): e for e in (raw.get("stacks") or [])}


def derive_stacks(ctx: Ctx) -> Draft:
    """Present modules adopted at the root their marker was found under; whatever the config
    already declares kept as written (its command overrides belong to block 4)."""
    d = Draft("stacks")
    entries = dict(_current_stacks(ctx.raw))
    for f in ctx.findings:
        if f.kind != "stack" or f.declined:
            continue
        d.evidence[f"stacks.{f.key}"] = ", ".join(f.evidence)
        if f.state == "ambiguous":
            d.owed.append(
                f"{f.name} at {f.root}: only the language marker ({', '.join(f.evidence)}) — "
                f"is this really {f.name}, or another tool's project?"
            )
        elif f.key not in entries:
            entries[f.key] = (
                f.name if f.root == "." else {"name": f.name, "root": f.root}
            )
    for key, entry in entries.items():
        # Asked of the module itself, not of the findings: an internal module (the harness's
        # own toolchain) is never a finding, and is still legitimately declared.
        try:
            present = _load_stack(entry).present()
        except ProjectError:
            present = False
        if not present:
            d.owed.append(
                f"{key} is declared but its markers are absent there — remove it, or fix its root"
            )
    d.values["stacks"] = list(entries.values())
    declined = (ctx.raw.get("declined") or {}).get("stacks")
    if declined:
        d.values["declined"] = {**(ctx.raw.get("declined") or {})}
    d.evidence["listing"] = {r: listing(r, ctx.repo) for r in ctx.roots}
    d.owed.append(
        "any toolchain in play that no module covers (read `evidence.listing`)"
    )
    return d


# --- block 3: frameworks ------------------------------------------------------------------


def derive_frameworks(ctx: Ctx) -> Draft:
    d = Draft("frameworks", declinable=True)
    d.values["frameworks"] = list(ctx.raw.get("frameworks") or [])
    for f in ctx.findings:
        if f.kind == "framework" and not f.declared and not f.declined:
            d.candidates.setdefault("frameworks", []).append(
                {"value": f.name, "source": ", ".join(f.evidence)}
            )
    d.evidence["dependencies"] = {
        r: deps for r in ctx.roots if (deps := dependencies(r, ctx.repo))
    }
    d.owed.append(
        "which candidates apply, and any framework in play no module covers (read `evidence.dependencies`)"
    )
    return d


# --- block 4: commands --------------------------------------------------------------------


def derive_commands(ctx: Ctx) -> Draft:
    """The existing probe-and-repair ladder (`check_commands`), run now and writing nothing:
    a working command is proved by running it, while the owner is still here to hear it."""
    from .check_commands import _broken_key, _first_working_candidate, _probe_reason

    d = Draft("commands")
    entries = list(
        ctx.stacks if ctx.stacks is not None else (ctx.raw.get("stacks") or [])
    )
    out = []
    for entry in entries:
        key = stack_key(entry)
        try:
            stack = _load_stack(entry)
        except ProjectError as exc:
            d.owed.append(f"{key}: {exc}")
            out.append(entry)
            continue
        reason = _probe_reason(stack, runner=ctx.runner)
        if reason is None:
            d.evidence[f"commands.{key}"] = "verify clean"
            out.append(entry)
            continue
        broken = _broken_key(stack)
        winner, tried = _first_working_candidate(stack, broken, runner=ctx.runner)
        if winner:
            fixed = dict(entry) if isinstance(entry, dict) else {"name": stack.name}
            fixed["commands"] = {**(fixed.get("commands") or {}), broken: winner}
            out.append(fixed)
            d.evidence[f"commands.{key}:{broken}"] = (
                f"proved by running ({reason} before)"
            )
        else:
            out.append(entry)
            d.owed.append(
                f"{key}: `{broken}` fails ({reason}); {len(tried)} candidate(s) from the repo, none worked"
            )
    d.values["stacks"] = out
    return d


# --- block 5: paths -----------------------------------------------------------------------


def derive_paths(ctx: Ctx) -> Draft:
    """Each role kept where the path exists — the current config's value first, else the
    template's. A role under a name nobody guessed is the owner's."""
    d = Draft("paths")
    tmpl = _template()
    for top, nested in (("paths", None), ("layout", "roles")):
        current = ctx.raw.get(top) or {}
        offered = tmpl.get(top) or {}
        if nested:
            current, offered = current.get(nested) or {}, offered.get(nested) or {}
        found: dict[str, str] = {}
        for role in {**offered, **current}:
            for value in (current.get(role), offered.get(role)):
                if value and (ctx.repo / str(value)).exists():
                    found[role] = str(value)
                    d.evidence[f"{top}.{role}"] = (
                        "exists"
                        if value == current.get(role)
                        else "exists (template's name)"
                    )
                    break
            else:
                d.owed.append(
                    f"{top}{'.' + nested if nested else ''}.{role} — where does this live, if anywhere?"
                )
        if found:
            d.values[top] = {nested: found} if nested else found
    return d


# --- block 6: tracker ---------------------------------------------------------------------


def _bead_prefix(repo: Path) -> tuple[str | None, str]:
    cfg = repo / ".beads" / "config.yaml"
    if cfg.is_file():
        try:
            d = yaml.safe_load(cfg.read_text()) or {}
            for key in ("issue-prefix", "issue_prefix", "prefix"):
                if d.get(key):
                    return str(d[key]), ".beads/config.yaml"
        except yaml.YAMLError:
            pass
    jsonl = repo / ".beads" / "issues.jsonl"
    if jsonl.is_file():
        for line in jsonl.read_text(errors="ignore").splitlines()[:50]:
            m = re.match(r'\{.*?"id"\s*:\s*"([A-Za-z][\w]*)-', line)
            if m:
                return m.group(1), ".beads/issues.jsonl"
    return None, ""


def missing_gitignore_lines(repo: Path) -> list[str]:
    """The required lines git does not already honour — asked of git itself, with a path
    each line must cover, so a broader rule (`.claude/`), a `**/` form or a parent
    repository's `.gitignore` all count, exactly as they will at commit time."""
    probes = {
        ln: (ln.replace("*", "-env") if "*" in ln else ln + "probe")
        for ln in GITIGNORE_LINES
    }
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "check-ignore", "--no-index", "--stdin"],
            input="\n".join(probes.values()),
            capture_output=True,
            text=True,
        )
    except OSError:
        return list(GITIGNORE_LINES)
    covered = {ln.strip() for ln in out.stdout.splitlines()}
    return [ln for ln, probe in probes.items() if probe not in covered]


def _beads_installed() -> bool:
    from tracker.beads import installed

    return installed()


def derive_tracker(ctx: Ctx) -> Draft:
    d = Draft("tracker")
    current = ctx.raw.get("tracker") or {}
    beads_here = (ctx.repo / ".beads").is_dir()
    if current:
        d.values["tracker"] = current
        d.evidence["tracker"] = "already declared"
    elif beads_here or _beads_installed():
        d.values["tracker"] = {"backend": "beads"}
        d.evidence["tracker.backend"] = ".beads/" if beads_here else "beads installed"
    else:
        d.owed.append(
            "the tracker: beads is not installed — install it, or choose `mdfiles` and name its export path"
        )
    slug = ctx.raw.get("slug") or slug_of(_name_sources(ctx.repo)[0][0])
    prefix, source = _bead_prefix(ctx.repo)
    prefix = (ctx.raw.get("beads") or {}).get("prefix") or prefix or slug.upper()
    d.values["beads"] = {**(ctx.raw.get("beads") or {}), "prefix": prefix}
    d.evidence["beads.prefix"] = source or (
        "already declared" if ctx.raw.get("beads") else "from the slug"
    )
    swarm = dict(ctx.raw.get("swarm") or {})
    swarm.setdefault("merge_slot", f"{prefix}-merge-slot")
    d.values["swarm"] = swarm
    missing = missing_gitignore_lines(ctx.repo)
    if missing:
        d.candidates["gitignore"] = [
            {"value": ln, "source": ".gitignore lacks it"} for ln in missing
        ]
        d.owed.append(
            f".gitignore needs {len(missing)} line(s) — confirm before they are added"
        )
    return d


# --- block 7: security -------------------------------------------------------------------

#: Words a boundary-shaped path tends to carry. ONLY ever used to build CANDIDATES shown
#: with their path, for the owner to react to — never a value, so this is the same posture
#: the spec takes for ports: declared by a human, never scraped.
BOUNDARY_WORDS = (
    "auth",
    "api",
    "admin",
    "billing",
    "payment",
    "webhook",
    "session",
    "token",
    "permission",
)


def _tracked(repo: Path) -> list[str]:
    out = _git(repo, "ls-files")
    return [ln for ln in out.splitlines() if ln]


def _env_names(repo: Path) -> list[tuple[str, str]]:
    """Variable NAMES from `.env.example` files, with file:line — never a value."""
    out = []
    for rel in sorted(
        p
        for p in _tracked(repo)
        if p.rsplit("/", 1)[-1] in (".env.example", ".env.sample", ".env.template")
    ):
        for n, ln in enumerate((repo / rel).read_text(errors="ignore").splitlines(), 1):
            m = re.match(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=", ln)
            if m:
                out.append((m.group(1), f"{rel}:{n}"))
    return out


def derive_security(ctx: Ctx) -> Draft:
    """Nothing is proposed here. A list of plausible rules is worse than a gap, because it
    reads as a decision; what the repository offers is a list to react to."""
    d = Draft("security")
    if ctx.raw.get("security"):
        d.values["security"] = ctx.raw["security"]
        d.evidence["security"] = "already declared"
    dirs = sorted(
        {
            "/".join(p.split("/")[: i + 1])
            for p in _tracked(ctx.repo)
            for i in range(min(2, p.count("/")))
        }
    )
    hits = [
        x
        for x in dirs
        if any(w in seg.lower() for seg in x.split("/") for w in BOUNDARY_WORDS)
    ]
    if hits:
        d.candidates["security.paths"] = [
            {"value": h + "/", "source": "path"} for h in hits[:20]
        ]
    names = _env_names(ctx.repo)
    if names:
        d.candidates["security.tokens"] = [
            {"value": n, "source": s} for n, s in names[:30]
        ]
    have = ctx.raw.get("security") or {}
    for key, question in (
        ("paths", "where does a change always deserve the security lens?"),
        ("tokens", "identifiers that mark sensitive data in a diff"),
        (
            "invariants",
            "what must never happen, stated as a rule (no proposals: outcomes only)",
        ),
    ):
        if not have.get(key):
            d.owed.append(f"security.{key} — {question}")
    return d


# --- block 8: areas -----------------------------------------------------------------------


def _churn(repo: Path) -> dict[str, int]:
    """Changes per depth-1 and depth-2 directory over recent history."""
    out: dict[str, int] = {}
    for ln in _git(repo, "log", "--name-only", "--format=", "-500").splitlines():
        parts = ln.split("/")
        for depth in (1, 2):
            if len(parts) > depth:
                key = "/".join(parts[:depth])
                out[key] = out.get(key, 0) + 1
    return out


def derive_areas(ctx: Ctx) -> Draft:
    d = Draft("areas")
    security_paths = [
        str(p).rstrip("/") for p in ((ctx.raw.get("security") or {}).get("paths") or [])
    ]
    if ctx.raw.get("areas"):
        d.values["areas"] = ctx.raw["areas"]
        d.evidence["areas"] = "already declared"
        return d
    churn = _churn(ctx.repo)
    top = [
        k
        for k in churn
        if "/" not in k and (ctx.repo / k).is_dir() and not k.startswith(".")
    ]
    if not top:
        top = [r for r in ctx.roots if r != "."]
    areas = []
    for path in sorted(top, key=lambda k: (-churn.get(k, 0), k))[:12]:
        area: dict[str, Any] = {"path": path, "label": path}
        if any(
            sp == path or sp.startswith(path + "/") or path.startswith(sp + "/")
            for sp in security_paths
        ):
            area["triggers"] = ["security"]
        areas.append(area)
        d.evidence[f"areas.{path}"] = (
            f"{churn.get(path, 0)} changes in the last 500 commits"
        )
    d.values["areas"] = areas
    if not areas:
        d.owed.append(
            "areas — no directory has history yet; name the parts of the repository"
        )
    return d


# --- block 9: testing and lenses ----------------------------------------------------------


def _is_test_file(name: str) -> bool:
    base = name.rsplit("/", 1)[-1].lower()
    return (
        base.startswith("test_")
        or "_test." in base
        or ".test." in base
        or ".spec." in base
    )


def derive_testing(ctx: Ctx) -> Draft:
    from .check_commands import _json_scripts, _make_targets

    d = Draft("testing")
    current = dict(ctx.raw.get("testing") or {})
    dirs: dict[str, int] = {}
    for p in _tracked(ctx.repo):
        if _is_test_file(p):
            parent = p.rsplit("/", 1)[0] if "/" in p else "."
            dirs[parent] = dirs.get(parent, 0) + 1
    layout = current.get("layout")
    if not layout and dirs:
        shown = sorted(dirs, key=lambda k: -dirs[k])[:6]
        layout = {"Tests": [f"`{x}/` ({dirs[x]} files)" for x in shown]}
        d.evidence["testing.layout"] = f"{sum(dirs.values())} test files"
    aggregates = current.get("aggregate_commands")
    if not aggregates:
        jobs = ("test", "lint", "typecheck", "check")
        found = [t for t in _make_targets() if t.split(" ", 1)[-1] in jobs]
        found += [f"npm run {s}" for s in _json_scripts("package.json") if s in jobs]
        if found:
            aggregates = {"Whole repo": " - ".join(f"`{c}`" for c in found)}
            d.evidence["testing.aggregate_commands"] = "Makefile / package.json"
    testing = {
        k: v
        for k, v in {
            **current,
            "layout": layout,
            "aggregate_commands": aggregates,
        }.items()
        if v
    }
    if testing:
        d.values["testing"] = testing
    d.values["lenses"] = ctx.raw.get("lenses") or {"additional": []}
    if "gates" not in current:
        d.owed.append(
            "testing.gates — the tests that must never be skipped or weakened"
        )
    if "coverage" not in current:
        d.owed.append("testing.coverage — the bar, if there is one")
    return d


# --- block 10: lanes, ports, fidelity -----------------------------------------------------


def _memory_gb() -> int:
    try:
        if sys.platform == "darwin":
            return (
                int(
                    subprocess.run(
                        ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True
                    ).stdout
                )
                // 2**30
            )
        import os

        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // 2**30
    except (OSError, ValueError):
        return 8


def _port_candidates(repo: Path) -> list[dict]:
    out: list[dict] = []
    for rel in _tracked(repo):
        base = rel.rsplit("/", 1)[-1]
        path = repo / rel
        if re.fullmatch(r"(docker-)?compose[\w.-]*\.ya?ml", base):
            for n, ln in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                m = re.match(r'^\s*-\s*["\']?(?:[\d.]+:)?(\d{2,5}):\d{2,5}', ln)
                if m:
                    out.append({"value": int(m.group(1)), "source": f"{rel}:{n}"})
        elif base in (".env.example", ".env.sample", "package.json"):
            for n, ln in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                m = re.search(
                    r"PORT\w*\s*=\s*(\d{2,5})|(?:--port[ =]|-p\s+)(\d{2,5})", ln
                )
                if m:
                    out.append(
                        {"value": int(m.group(1) or m.group(2)), "source": f"{rel}:{n}"}
                    )
    try:
        lsof = subprocess.run(
            ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        for ln in lsof.splitlines()[1:]:
            m = re.search(r":(\d+) \(LISTEN\)", ln)
            if m:
                out.append(
                    {
                        "value": int(m.group(1)),
                        "source": f"listening now ({ln.split()[0]})",
                    }
                )
    except (OSError, subprocess.TimeoutExpired):
        pass
    seen, uniq = set(), []
    for c in out:
        if (c["value"], c["source"]) not in seen:
            seen.add((c["value"], c["source"]))
            uniq.append(c)
    return uniq


def derive_lanes(ctx: Ctx) -> Draft:
    import os

    d = Draft("lanes", declinable=True)
    if ctx.raw.get("lanes"):
        d.values["lanes"] = ctx.raw["lanes"]
        d.evidence["lanes"] = "already declared"
    else:
        cores, mem = os.cpu_count() or 2, _memory_gb()
        cap = max(1, min(cores // 2, mem // 4, 4))
        constraint = f"unmeasured — derived from {cores} cores / {mem} GB at setup; measure before raising"
        lanes: dict[str, dict] = {}
        frameworks = list(ctx.raw.get("frameworks") or [])
        for entry in (
            ctx.stacks if ctx.stacks is not None else (ctx.raw.get("stacks") or [])
        ):
            name = entry["name"] if isinstance(entry, dict) else str(entry)
            root = (entry.get("root") if isinstance(entry, dict) else None) or "."
            lane = name if root == "." else root.replace("/", "-")
            lanes[lane] = {
                "stacks": [name],
                "agent": "fullstack-engineer",
                "cap": cap,
                "constraint": constraint,
            }
            if frameworks:
                lanes[lane]["frameworks"] = frameworks
        lanes["docs"] = {"cap": 1}
        d.values["lanes"] = lanes
        d.evidence["lanes"] = f"one per stack, cap {cap} ({cores} cores, {mem} GB)"
        d.owed.append(
            "lane caps — the derived cap is unmeasured; a real cap is measured on this machine"
        )
    if "ports" in ctx.raw:
        d.values["ports"] = ctx.raw["ports"]
        d.evidence["ports"] = "already declared"
    else:
        cands = _port_candidates(ctx.repo)
        if cands:
            d.candidates["ports"] = cands
        d.owed.append(
            "ports — every TCP port this project's servers bind, by name (`{}` if none)"
        )
    if ctx.raw.get("fidelity"):
        d.values["fidelity"] = ctx.raw["fidelity"]
    else:
        d.owed.append(
            "fidelity — is there a design handover to compare screens against? (usually no)"
        )
    return d


# --- block 11: domain and signals ---------------------------------------------------------


def _megafile_lines(repo: Path) -> int:
    lengths = []
    for rel in _tracked(repo):
        base = rel.rsplit("/", 1)[-1].lower()
        if "lock" in base or base.endswith((".md", ".json", ".svg", ".png", ".jpg")):
            continue
        try:
            lengths.append(len((repo / rel).read_text(errors="strict").splitlines()))
        except (OSError, UnicodeDecodeError):
            continue
    if not lengths:
        return 1000
    lengths.sort()
    p99 = lengths[min(len(lengths) - 1, int(len(lengths) * 0.99))]
    return max(500, -(-p99 // 100) * 100)


def derive_domain(ctx: Ctx) -> Draft:
    from .wave_report import escape_rate

    d = Draft("domain", declinable=True)
    domain = dict(ctx.raw.get("domain") or {})
    domain.setdefault("nouns", [])
    if "derive_from" not in domain:
        paths = ctx.raw.get("paths") or derive_paths(ctx).values.get("paths") or {}
        sources = [paths[r] for r in ("features", "glossary") if r in paths]
        if sources:
            domain["derive_from"] = sources
            d.evidence["domain.derive_from"] = "paths.features / paths.glossary"
    d.values["domain"] = domain
    signals = dict(ctx.raw.get("signals") or {})
    if not signals:
        esc, total = escape_rate(ctx.repo)
        tmpl = (_template().get("signals") or {}).get("baselines") or {}
        signals = {
            "baselines": {
                "escape_rate": round(esc / total, 2) if total else None,
                "first_pass_ceiling": tmpl.get("first_pass_ceiling", 0.90),
                "first_pass_floor": tmpl.get("first_pass_floor", 0.40),
            },
            "megafile_lines": _megafile_lines(ctx.repo),
        }
        d.evidence["signals.baselines.escape_rate"] = (
            f"{esc} fix/revert of {total} commits"
        )
        d.evidence["signals.megafile_lines"] = (
            "the 99th percentile of tracked file lengths"
        )
    d.values["signals"] = signals
    if not domain["nouns"]:
        d.owed.append(
            "domain.nouns — entity names an agent must never invent here (empty is legitimate)"
        )
    return d


# --- block 12: models ---------------------------------------------------------------------


def derive_models(ctx: Ctx) -> Draft:
    d = Draft("models")
    d.evidence["models"] = (
        "nothing to set: the plugin's shipped strengths and activities apply until you have "
        "measured a reason to patch them"
    )
    return d


# --- the stack breakout: authoring a module for a toolchain no module covers ---------------

#: Below this, restoring per worker is cheap enough to do every time; above it, a symlink to
#: the primary checkout's dependencies saves minutes per worker per wave. Shown with the
#: measured number either way, so the owner can overrule it.
INSTALL_SECONDS = 60


def _du(path: Path) -> int:
    try:
        return int(
            subprocess.run(
                ["du", "-sk", str(path)], capture_output=True, text=True
            ).stdout.split()[0]
        )
    except (OSError, ValueError, IndexError):
        return 0


def derive_stack_breakout(root: str, ctx: Ctx | None = None) -> Draft:
    """Pick-lists for authoring a stack module at `root`: every value drawn from what the
    repository already holds, and two — the markers and `env` — left for the owner to choose
    from rather than type."""
    from .check_commands import _ci_commands, _json_scripts, _make_targets

    ctx = ctx or context()
    base = ctx.repo if root == "." else ctx.repo / root
    d = Draft("stack-breakout")
    files = [f for f in listing(root, ctx.repo) if not f.endswith("/")]
    d.candidates["detect_any"] = [
        {"value": f, "source": "pick the file that marks THIS tool, not the language"}
        for f in files
    ]
    d.candidates["detect_language"] = [
        {"value": f, "source": "pick the language's own manifest, if any"}
        for f in files
    ]
    present = (
        [p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")]
        if base.is_dir()
        else []
    )
    rel = [p.name if root == "." else f"{root}/{p.name}" for p in present]
    ignored = set(_ignored_dirs(rel, ctx.repo))
    deps = sorted(
        ((_du(p), p.name) for p, r in zip(present, rel) if r in ignored), reverse=True
    )
    if deps:
        d.values["dependency_dir"] = deps[0][1]
        d.evidence["dependency_dir"] = f"gitignored, {deps[0][0] // 1024} MB"
        d.candidates["dependency_dir"] = [
            {"value": n, "source": f"gitignored, {k // 1024} MB"} for k, n in deps[1:]
        ]
    restore = re.compile(r"\b(install|sync|ci|restore|fetch|download)\b")
    lines = [c for c in _ci_commands() if restore.search(c)]
    lines += [t for t in _make_targets() if restore.search(t)]
    lines += [
        f"npm run {s}"
        for s in _json_scripts(
            f"{root}/package.json" if root != "." else "package.json"
        )
        if restore.search(s)
    ]
    if lines:
        d.candidates["bootstrap.command"] = [
            {"value": c, "source": "CI / Makefile / package.json"}
            for c in dict.fromkeys(lines)
        ]
    else:
        d.owed.append(
            "bootstrap.command — how a fresh checkout restores its dependencies"
        )
    names = [n for n, _ in _env_names(ctx.repo)]
    if names:
        d.candidates["env"] = [{"value": n, "source": ".env.example"} for n in names]
    d.owed += [
        "detect_any / detect_language — picked from the listing, never typed",
        "env — what a parallel worker must NOT share (a database, a cache); siblings sharing one still pass their suite",
        "banned_forms — owed whenever `env` is non-empty: the command forms that would bypass it",
        "card — the prohibitions a worker in this toolchain must not break, within the card budget",
    ]
    return d


def _ignored_dirs(rel: list[str], repo: Path) -> list[str]:
    from .discover import _ignored

    return sorted(_ignored(rel, repo))


def time_bootstrap(
    command: str, root: str = ".", repo: Path | None = None, timeout: int | None = None
) -> dict:
    """Run `command` in a scratch worktree of HEAD and time it — `bootstrap.strategy` on
    evidence, not habit. The worktree is removed whatever happens."""
    import tempfile
    import time

    from .commands import PROBE_TIMEOUT

    repo = repo or _project.REPO
    scratch = Path(tempfile.mkdtemp(prefix="mad-bootstrap-")) / "wt"
    added = subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", "-q", str(scratch)],
        capture_output=True,
        text=True,
    )
    if added.returncode != 0:
        return {"seconds": None, "ok": False, "detail": added.stderr.strip()[:200]}
    try:
        started = time.monotonic()
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(scratch if root == "." else scratch / root),
                capture_output=True,
                text=True,
                timeout=timeout or PROBE_TIMEOUT * 10,
            )
            ok, detail = (
                proc.returncode == 0,
                (proc.stderr or proc.stdout).strip()[-200:],
            )
        except subprocess.TimeoutExpired:
            ok, detail = False, "timed out"
        seconds = round(time.monotonic() - started, 1)
    finally:
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "remove", "--force", str(scratch)],
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "prune"], capture_output=True
        )
        shutil.rmtree(scratch.parent, ignore_errors=True)
    return {
        "seconds": seconds,
        "ok": ok,
        "detail": detail,
        "recommend": "install" if seconds < INSTALL_SECONDS else "symlink",
        "threshold": INSTALL_SECONDS,
    }


# --- the surface --------------------------------------------------------------------------

DERIVERS = {
    "identity": derive_identity,
    "stacks": derive_stacks,
    "frameworks": derive_frameworks,
    "commands": derive_commands,
    "paths": derive_paths,
    "tracker": derive_tracker,
    "security": derive_security,
    "areas": derive_areas,
    "testing": derive_testing,
    "lanes": derive_lanes,
    "domain": derive_domain,
    "models": derive_models,
}


def derive_all(ctx: Ctx) -> list[Draft]:
    """Every block's draft, in registry order. Later blocks build on the stacks decision
    block 2 just derived, as the owner would after confirming it."""
    out = []
    for block_id, fn in DERIVERS.items():
        draft = fn(ctx)
        if block_id == "stacks" and ctx.stacks is None:
            ctx.stacks = draft.values.get("stacks") or []
        out.append(draft)
    return out


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "stack-breakout":
        root = args[args.index("--root") + 1] if "--root" in args else "."
        if "--time-bootstrap" in args:
            print(
                json.dumps(
                    time_bootstrap(args[args.index("--time-bootstrap") + 1], root),
                    indent=2,
                )
            )
        else:
            print(
                json.dumps(asdict(derive_stack_breakout(root)), indent=2, default=str)
            )
        return 0
    ctx = context()
    if "--block" in args:
        block_id = args[args.index("--block") + 1]
        if block_id not in DERIVERS:
            print(
                f"no derivation for block {block_id!r}; known: {', '.join(DERIVERS)}",
                file=sys.stderr,
            )
            return 2
        drafts = [DERIVERS[block_id](ctx)]
    else:
        drafts = derive_all(ctx)
    print(json.dumps([asdict(dr) for dr in drafts], indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
