"""Verify the project config, and that the places still hard-coding it agree.

A config extracted from code is only an improvement while the two agree. The
moment `harness.yaml` says one thing and `swarm-worktree-init.sh` does another,
there are two truths and a reader cannot tell which is live — strictly worse than
the single hard-coded truth we started from.

So this check is deliberately *not* only a schema validator. It reads what the
config declares and asserts the hard-coded places match, which lets the rewiring
happen incrementally without the config ever being decorative. It is the same
shape as `check-model-config.sh`, which does this for tiers versus frontmatter.
"""

from __future__ import annotations

import sys

from .project import ProjectError, load, plugin_version, upgrade_status
from .resolve import HARNESS, REPO, ConfigError, _prompts_dir

INIT_SCRIPT = HARNESS / "swarm" / "swarm-worktree-init.sh"
SWARM_DOC = _prompts_dir("commands") / "swarm.md"


def _lane(p) -> str:
    """The project's first declared lane. `backend` is one project's vocabulary, and
    `worker.default_lane()` was added specifically to stop hardcoding it."""
    return next(iter(p.raw.get("lanes") or {}), "default")


#: `--strict` turns an UPGRADE finding into a failing exit. The pre-flights use it: a
#: campaign must not start on a config nobody has reviewed against the plugin running it.
EXIT_UPGRADE = 3


def main(argv: list[str] | None = None) -> int:
    strict = "--strict" in (sys.argv[1:] if argv is None else argv)
    try:
        p = load()
    except ProjectError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    failures: list[str] = []
    warnings: list[str] = []
    upgrade: str | None = None

    print(f"project: {p.name} ({p.slug})")

    # THE STAMP IS HOW A PROJECT LEARNS THE PLUGIN MOVED. The cache is replaced wholesale
    # on `claude plugin update`, ships no hook, and changes nothing in the project — so
    # without this line a config written for 0.9.0 runs under 0.9.1 forever, missing every
    # block the new version reads. Its own category rather than a warning: warnings are
    # advisory everywhere, and this one blocks a pre-flight.
    try:
        installed = plugin_version()
        stamped = p.stamped_version()
        status = upgrade_status(stamped, installed)
        print(f"harness: {stamped or '(unstamped)'}  installed plugin {installed}")
        if status == "unstamped":
            upgrade = (
                f"harness.yaml carries no `harness.version`, so it has never been reviewed "
                f"against any plugin version (installed: {installed}). Run /harness-setup — "
                f"it applies every upgrade note and stamps the config."
            )
        elif status == "behind":
            upgrade = (
                f"harness.yaml was written for plugin {stamped}; {installed} is installed. "
                f"Run /harness-setup — it applies the upgrade notes between the two and "
                f"re-stamps the config."
            )
        elif status == "patch-behind":
            warnings.append(
                f"harness.yaml is stamped {stamped}; {installed} is installed. A patch "
                f"release changes nothing the config must say — re-stamp it when "
                f"convenient (`harness: {{version: {installed}}}`, or /harness-setup)."
            )
        elif status == "ahead":
            warnings.append(
                f"harness.yaml is stamped {stamped} but the installed plugin is {installed} "
                f"— the PLUGIN is behind. `claude plugin update` it."
            )
    except ProjectError as exc:
        failures.append(str(exc))
    print(f"paths:   {', '.join(f'{k}={v}' for k, v in sorted(p.paths.items()))}")

    for key, rel in sorted(p.paths.items()):
        if not (REPO / rel).exists():
            failures.append(f"paths.{key} = {rel!r} does not exist")

    # The tracker is the one config block whose failure is invisible until a wave is
    # already running, so it is validated here rather than at first use.
    try:
        tk = p.tracker()
        limit = (tk.get("limits") or {}).get("record_bytes")
        print(
            f"tracker: {tk['backend']}"
            + (f"  dir={tk['dir']}" if tk.get("dir") else "")
            + (f"  export={tk['export']}" if tk.get("export") else "")
            + (f"  record_bytes={limit}" if limit else "")
        )
    except ProjectError as exc:
        failures.append(str(exc))

    try:
        levers_on = p.dispatch()
        if levers_on:
            print(f"levers:  {', '.join(f'{k}={v}' for k, v in sorted(levers_on.items()))}")
    except ProjectError as exc:
        failures.append(str(exc))

    # A tier override is a project running an A/B arm; it is shown so a reader of this
    # output knows which agents are off the plugin's defaults, and named as an override
    # so nobody mistakes it for the agent's own declaration.
    try:
        moved = p.tiers()
        if moved:
            print(f"tiers:   {', '.join(f'{k}->{v}' for k, v in sorted(moved.items()))}  (project overrides)")
    except (ProjectError, ConfigError) as exc:
        failures.append(str(exc))

    try:
        ports = p.ports()
        if ports:
            print(f"ports:   {', '.join(f'{k}={v}' for k, v in sorted(ports.items()))}")
        elif p.stacks and "ports" not in p.raw:
            # A project with a toolchain almost always has a server; a config written
            # before `ports:` existed has no way of knowing it is now expected. This is
            # the signal /harness-setup's "repair" trigger listens for — without it the
            # pre-flight would say "none declared" forever and nobody would be sent here.
            # An explicit `ports: {}` is an answer — nothing listens — and is not warned.
            warnings.append(
                "no ports declared. If this project's servers bind any, declare them "
                "under `ports:` so the pre-flight can catch one left running — see "
                "templates/harness.yaml.example."
            )
    except ProjectError as exc:
        failures.append(str(exc))

    try:
        archive = p.archive_dir()
        if archive:
            print(f"archive: {archive}  (spent epic folders are moved here, not deleted)")
    except ProjectError as exc:
        failures.append(str(exc))

    print("\nstacks:")
    for s in p.stacks:
        if s.present():
            state = "present"
        elif s.ambiguous():
            state = "AMBIGUOUS"
        else:
            state = "NOT DETECTED"
        print(
            f"  {s.name:<14} {state:<13} root={s.root:<10} deps={s.deps_path}  "
            f"env={sorted(s.env)}"
        )
        if s.ambiguous():
            # The language is here but the toolchain's own marker is not. A warning
            # rather than a failure: a project may legitimately gitignore its lockfile.
            # Silence would be wrong though — this is exactly the shape that passes
            # config validation and then fails inside a worker's worktree mid-wave.
            warnings.append(
                f"stack {s.name!r}: found {list(s.detect_language)} under root "
                f"{s.root!r} but none of {list(s.detect_any)}. That marks the LANGUAGE, "
                f"not this toolchain — another tool may own this project. Confirm it is "
                f"really {s.name!r}, or the bootstrap will fail inside a worker."
            )
        elif not s.present():
            failures.append(
                f"stack {s.name!r} is declared but none of its markers "
                f"{list(s.detect_any)} exist under root {s.root!r} — either the project "
                f"does not use it, or its `root` is wrong for this layout"
            )

    print(f"\nareas: {len(p.areas)}")
    for a in p.areas:
        if not (REPO / a.path).exists():
            warnings.append(f"area path {a.path!r} does not exist (stale layout?)")
    trig = sorted({t for a in p.areas for t in a.triggers})
    print(f"  triggers declared: {trig or '(none)'}")

    # The hard-coded places must agree with what the config declares.
    # The worker env is now generated from these stacks rather than typed into the
    # script, so verify the generated block rather than grepping a thin wrapper.
    from pathlib import Path as _P

    from .worker import env_block

    block = env_block(1, _lane(p), _P("/main"), p)
    for var in sorted(p.worker_env(1)):
        if f"export {var}=" not in block:
            failures.append(
                f"the generated .swarm-env omits {var}, which stack config declares a "
                f"worker needs. A worker missing it falls through to a shared default "
                f"and corrupts its siblings silently."
            )
    if env_block(1, _lane(p), _P("/m"), p) == env_block(2, _lane(p), _P("/m"), p):
        failures.append(
            "workers 1 and 2 generate an identical env — they would share resources"
        )

    # The prose used to LIST the security tokens, so this checked the two agreed.
    # It now delegates to `security.tokens` instead, which is the correct direction —
    # so the check is that the delegation is still there, not that the list is echoed.
    # Comparing against the old prose produced a warning on a correctly-configured
    # repo, which is the kind of noise that teaches people to ignore a check.
    if SWARM_DOC.exists():
        doc = SWARM_DOC.read_text()
        if "security.tokens" not in doc:
            warnings.append(
                f"{SWARM_DOC.name} no longer points at `security.tokens` for the L4 "
                f"trigger — the lens fires on what the prose says, so the config is "
                f"aspirational until they agree"
            )

    if warnings:
        print("\nWARN:", file=sys.stderr)
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)
    if upgrade:
        print(f"\nUPGRADE: {upgrade}", file=sys.stderr)
    if failures:
        print("\nFAIL:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    if upgrade and strict:
        print("\nBLOCKED — the config predates the installed plugin; see UPGRADE above.")
        return EXIT_UPGRADE
    if upgrade:
        print(
            f"\nUPGRADE PENDING — config valid for now, {len(p.stacks)} stacks present, "
            f"but unreviewed against the installed plugin. Advisory here; a pre-flight "
            f"runs this with --strict and stops."
        )
        return 0
    if warnings:
        print(
            f"\nWARN — config valid, {len(p.stacks)} stacks present, "
            f"{len(warnings)} drift warning(s) above."
        )
        return 0
    print(
        f"\nOK — config valid, {len(p.stacks)} stacks present, "
        f"{len(p.areas)} areas, hard-coded sites agree."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
