"""Generate the documentation's enumerable parts, and fail when they drift.

WHY THIS EXISTS. Parts of `docs/` are hand-written mirrors of data that already exists in
structured form: which agents ship and what tier each declares, which skills exist, which
commands, which checks. A mirror maintained by hand drifts, and the drift is invisible —
the page still reads correctly, it is simply wrong. Writing those pages, a tier was
documented as `max` when the tier is called `strategic`, and a script was cited that does
not exist. Both were caught by someone choosing to look.

Same shape as `check_config.py` and `tk.sh render --check`: generated from the source of
truth, with a `--check` mode that fails the build on any difference. The prose around each
table stays hand-written — an argument is not data, and encoding it as data would strip the
causation that makes a rule survive.

AND `docs/llms.txt`, which is the retrieval half. An agent facing twenty pages has no cheap
way to find the right one, and reading them all is the expensive failure. The `llms.txt`
convention is a flat machine-readable index — one small file that names every page and what
it answers, so an agent reads it and then ONE page.

    harness/checks/check-docs.sh            report drift, exit non-zero on any
    harness/checks/check-docs.sh --write    regenerate
"""

from __future__ import annotations

import json
import re
import sys
import textwrap
from pathlib import Path

from .resolve import AGENTS_DIR, PLUGIN_ROOT, agent_frontmatter

DOCS = PLUGIN_ROOT / "docs"

#: A generated block is delimited so the prose around it stays hand-written. Both markers
#: carry the regeneration command, because the reader who finds a stale table is the one
#: who needs to know how to fix it.
START = "<!-- GENERATED:{key} — do not hand-edit; run harness/checks/check-docs.sh --write -->"
END = "<!-- /GENERATED:{key} -->"


def _frontmatter_desc(path: Path) -> str:
    for line in path.read_text().splitlines():
        if line.startswith("description:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    return ""


def _first_comment(path: Path) -> str:
    """The purpose line of a shell check — its second line, by convention."""
    lines = path.read_text().splitlines()
    for line in lines[1:5]:
        if line.startswith("#") and line.strip() != "#":
            return line.lstrip("# ").strip()
    return ""


def agents_table() -> str:
    rows = ["| agent | tier | declares |", "|---|---|---|"]
    for f in sorted(AGENTS_DIR.glob("*.md")):
        fm = agent_frontmatter(f.stem)
        tier = str(fm.get("model_tier") or "—")
        tools = str(fm.get("tools") or "")
        kind = "writer" if ("Edit" in tools or "Write" in tools) else "reader"
        iso = " · worktree" if fm.get("isolation") == "worktree" else ""
        rows.append(f"| `{f.stem}` | {tier} | {kind}{iso} |")
    return "\n".join(rows)


def skills_table() -> str:
    rows = ["| skill | carries |", "|---|---|"]
    for d in sorted((PLUGIN_ROOT / "skills").iterdir()):
        sk = d / "SKILL.md"
        if sk.is_file():
            rows.append(f"| [`{d.name}`](../../skills/{d.name}/SKILL.md) | {_frontmatter_desc(sk)[:96]} |")
    return "\n".join(rows)


def commands_table() -> str:
    rows = ["| command | does |", "|---|---|"]
    for f in sorted((PLUGIN_ROOT / "commands").glob("*.md")):
        rows.append(f"| `/{f.stem}` | {_frontmatter_desc(f)[:110]} |")
    return "\n".join(rows)


def checks_table() -> str:
    rows = ["| check | enforces |", "|---|---|"]
    for f in sorted((PLUGIN_ROOT / "harness" / "checks").glob("*.sh")):
        rows.append(f"| `{f.name}` | {_first_comment(f)[:104]} |")
    return "\n".join(rows)


def modules_table() -> str:
    """The stack and framework modules that SHIP.

    Generated because the honest answer is small and a reader has to have it before they
    adopt: the docs used to link the directory, so "which toolchains are supported" was a
    click away rather than on the page, and a hand-written list of four would drift the
    first time a fifth landed.
    """
    import yaml

    rows = ["| module | axis | for |", "|---|---|---|"]
    for axis in ("stacks", "frameworks"):
        for f in sorted((PLUGIN_ROOT / "harness" / axis).glob("*.yaml")):
            if f.stem.startswith("_") or f.stem.endswith("-selftest"):
                continue  # the schema template, and this repo's own test fixture
            try:
                desc = (yaml.safe_load(f.read_text()) or {}).get("description", "")
            except yaml.YAMLError:
                desc = ""
            rows.append(f"| `{f.stem}` | {axis[:-1]} | {str(desc)[:88]} |")
    return "\n".join(rows)


GENERATORS = {
    "agents": agents_table,
    "skills": skills_table,
    "commands": commands_table,
    "checks": checks_table,
    "modules": modules_table,
}


def llms_txt() -> str:
    """The retrieval index, in the `llms.txt` convention."""
    # FROM THE MANIFEST, NOT HARDCODED. The harness must not name this project anywhere
    # outside its config — `test_no_project_identifier_appears_in_the_harness_outside_the_config`
    # caught exactly that here, and it is the whole reusability claim being asserted rather
    # than believed. The plugin's own manifest is the legitimate place for its identity.
    manifest = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())
    out = [
        f"# {manifest.get('name', 'harness')}",
        "",
        *[f"> {line}" for line in _wrap(manifest.get("description", ""))],
        "",
        "GENERATED by harness/checks/check-docs.sh — do not hand-edit.",
        "",
        "## Documentation",
        "",
    ]
    for page in sorted(DOCS.glob("**/*.md")):
        rel = page.relative_to(DOCS)
        if rel.name == "README.md" and rel.parent == Path("."):
            continue
        title, summary = _page_summary(page)
        out.append(f"- [{title}](docs/{rel}): {summary}")

    out += [
        "",
        "## Doctrine (loaded by agents on demand, not browsed)",
        "",
    ]
    for d in sorted((PLUGIN_ROOT / "skills").iterdir()):
        sk = d / "SKILL.md"
        if sk.is_file():
            out.append(f"- [{d.name}](skills/{d.name}/SKILL.md): {_frontmatter_desc(sk)[:110]}")

    out += [
        "",
        "## Contracts (the machine-readable source of truth)",
        "",
        "- [harness/tracker/port.py](harness/tracker/port.py): the four tracker ports",
        "- [harness/models/tiers.yaml](harness/models/tiers.yaml): model tiers",
        "- [templates/harness.yaml.example](templates/harness.yaml.example): every config block, annotated",
        "- [harness/stacks/_template.yaml](harness/stacks/_template.yaml): the stack module schema",
        "",
    ]
    return "\n".join(out) + "\n"


def _wrap(text: str, width: int = 88) -> list[str]:
    return textwrap.wrap(text, width) or [""]


def _page_summary(page: Path) -> tuple[str, str]:
    """A page's title and its first sentence of prose."""
    lines = page.read_text().splitlines()
    title = next((ln.lstrip("# ").strip() for ln in lines if ln.startswith("# ")), page.stem)
    for ln in lines:
        s = ln.strip()
        if s and not s.startswith(("#", "|", ">", "-", "*", "<!--", "```")):
            first = re.split(r"(?<=[.!?]) ", s)[0]
            # Truncate on a WORD boundary. A summary cut mid-word reads as corruption and
            # tells a reader less than a shorter clean one.
            if len(first) > 150:
                first = first[:150].rsplit(" ", 1)[0] + "…"
            return title, first
    # An index page is all table and has no prose line; its title is the summary.
    return title, f"index of {title.lower()}"


def _render(text: str, key: str, body: str) -> str:
    start, end = START.format(key=key), END.format(key=key)
    block = f"{start}\n\n{body}\n\n{end}"
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    return pattern.sub(lambda _: block, text) if start in text else text


TARGETS = {
    "agents": DOCS / "reference" / "agents.md",
    "skills": DOCS / "reference" / "skills.md",
    "commands": DOCS / "reference" / "commands.md",
    "checks": DOCS / "reference" / "checks.md",
    "modules": DOCS / "reference" / "stacks.md",
}
#: TWO LISTS THAT MUST AGREE. A generator with no target is never called and a target with
#: no generator would raise; the first is the dangerous one, because the page keeps its
#: EMPTY generated block and the check still reports OK. Measured: `modules` was written,
#: the block was added to the page, `--write` ran, and the table stayed empty and clean.
assert set(GENERATORS) == set(TARGETS), (
    f"generators and targets disagree: {set(GENERATORS) ^ set(TARGETS)}"
)


def _short(path: Path) -> str:
    """A path relative to the plugin when it is inside one, and itself otherwise.

     RAISES rather than returning the input, so a target outside the plugin —
    a test fixture, most obviously — crashed the reporter instead of being named.
    """
    try:
        return str(path.relative_to(PLUGIN_ROOT))
    except ValueError:
        return str(path)


#: `--layout tala` — d2's own engine, chosen over the default after rendering the same
#: sources through all three. dagre laid the loop out as a 6.7:1 strip (3245x485), which
#: is what forced its side nodes out to the edges with edges crossing back; TALA produced
#: 1.0:1 (1022x1023) and no crossings. ELK sits between them. Verified deterministic across
#: repeated runs, which the drift check below depends on — a renderer that varied would
#: report drift on every invocation. The default `--tala-seeds 1,2,3` was compared against
#: eight seeds and found identical, so the extra attempts buy nothing here.
#:
#: `--theme 0 --dark-theme 200` embeds BOTH palettes behind a `prefers-color-scheme` media
#: query inside each SVG, so one file renders correctly on a light and a dark GitHub.
#: `--pad 20` keeps edge labels off the viewBox boundary.
D2_ARGS = ["--layout", "tala", "--theme", "0", "--dark-theme", "200", "--pad", "20"]


def _diagrams(write: bool) -> list[str]:
    """Re-render every `docs/assets/src/*.d2` and report SVGs that no longer match.

    THE SOURCE IS THE D2, NOT THE SVG. A hand-edited SVG is unmaintainable — nobody edits
    path coordinates — so the generated file is checked against its source the same way
    `model:`/`effort:` are checked against `tiers.yaml`.

    d2 is a contributor dependency rather than a runtime one: absent, this reports that it
    cannot verify instead of silently passing, because a check that quietly does nothing is
    worse than no check.
    """
    import shutil
    import subprocess

    src_dir = DOCS / "assets" / "src"
    if not src_dir.is_dir():
        return []
    if not shutil.which("d2"):
        print("note: d2 is not installed — diagram drift NOT verified", file=sys.stderr)
        return []

    stale: list[str] = []
    for src in sorted(src_dir.glob("*.d2")):
        out = DOCS / "assets" / f"{src.stem}.svg"
        proc = subprocess.run(
            ["d2", *D2_ARGS, str(src), "-"], capture_output=True, text=True
        )
        if proc.returncode != 0:
            stale.append(f"{_short(src)} (d2 failed: {proc.stderr.strip()[:120]})")
            continue
        rendered = proc.stdout
        if not out.is_file() or out.read_text() != rendered:
            stale.append(_short(out))
            if write:
                out.write_text(rendered)
    return stale


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    write = "--write" in argv
    drifted: list[str] = []

    for key, target in TARGETS.items():
        if not target.is_file():
            continue
        current = target.read_text()
        updated = _render(current, key, GENERATORS[key]())
        if updated != current:
            drifted.append(_short(target))
            if write:
                target.write_text(updated)

    drifted += _diagrams(write)

    index = DOCS / "llms.txt"
    want = llms_txt()
    if not index.is_file() or index.read_text() != want:
        drifted.append("docs/llms.txt")
        if write:
            index.write_text(want)

    if not drifted:
        print("OK — generated documentation matches its sources.")
        return 0
    if write:
        print("rewrote:\n  " + "\n  ".join(drifted))
        return 0
    print("DRIFT — these no longer match the code they describe:", file=sys.stderr)
    print("  " + "\n  ".join(drifted), file=sys.stderr)
    print("\nRegenerate: harness/checks/check-docs.sh --write", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
