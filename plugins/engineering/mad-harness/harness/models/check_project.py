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

import re
import sys
from pathlib import Path

from .project import PROJECT_FILE, ProjectError, load, plugin_version, upgrade_status
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


STAMP = re.compile(r"^(?P<indent>[ \t]*)version:[ \t]*\S+[ \t]*$", re.M)


def stamp(path: Path, installed: str) -> str:
    """Write `harness.version: <installed>` into the config IN PLACE — the one line
    /harness-setup and every upgrade note end with, which nothing scripted did. The
    value is read from the plugin manifest, never typed: an unbumped or mistyped stamp
    is a config that reads as current while missing every block the version reads.
    Returns what happened."""
    text = path.read_text()
    m = re.search(r"^harness:[ \t]*\n((?:[ \t]+.*\n?)*)", text, re.M)
    if m:
        block = m.group(0)
        if STAMP.search(block):
            new_block = STAMP.sub(lambda mm: f"{mm.group('indent')}version: {installed}", block, count=1)
        else:
            new_block = block.rstrip("\n") + f"\n  version: {installed}\n"
        text = text[: m.start()] + new_block + text[m.end():]
        what = "re-stamped"
    else:
        text = text.rstrip("\n") + f"\n\nharness:\n  version: {installed}\n"
        what = "stamped (a harness: block was added)"
    path.write_text(text)
    return f"{what} harness.version: {installed}"



def _collapsed_strengths(cfg: dict) -> list[str]:
    """Ladder rungs that escalate to the same thing, because effort is all that separates
    them and the provider may not act on it.

    `strong` and `strategic` are the SAME model and differ only in `effort`, which is what
    makes the top of the ladder meaningful on Anthropic. Routed at a provider that ignores
    the parameter, the rung becomes a no-op: a stage that escalated because it needed deeper
    deliberation is re-run with exactly what it already had, at the same price, and reports
    success. Nothing else would notice.

    MEASURED (2026-09-24), one prompt, five runs per level against deepseek-v4-pro: output
    tokens by effort were low 181, high 178, max 215 (medians; ranges 173-235, 155-211,
    195-426) — low and high indistinguishable and every pair's spread overlapping, so by
    this project's own standard there is no effect to report. `thinking_tokens` was 0 at
    every level on every run. The same probe on claude-opus-5[1m] moved 156 -> 384 -> 583
    output and 39 -> 113 -> 299 thinking, so the instrument reads the effect where there is
    one. One provider and one prompt, hence a warning and not a refusal.
    """
    strengths = cfg.get("strengths") or {}
    out: list[str] = []
    # EVERY PAIR, not ladder adjacency. The ladder is gone, and all-pairs is both more
    # coverage and less code: two strengths that differ only by `thinking` are a
    # distinction-without-a-difference off Anthropic wherever they sit in a chain.
    names = sorted(strengths)
    for i, lower in enumerate(names):
        for upper in names[i + 1:]:
            a, b = strengths.get(lower) or {}, strengths.get(upper) or {}
            if (a.get("provider"), a.get("model")) != (b.get("provider"), b.get("model")):
                continue
            if a.get("provider") == "anthropic" or a.get("thinking") == b.get("thinking"):
                continue
            using = sorted(
                aid for aid, spec in (cfg.get("activities") or {}).items()
                if {lower, upper} <= set(_all_chain_members(spec))
            )
            out.append(
                f"strengths {lower!r} and {upper!r} are the same model "
                f"({a.get('provider')}/{a.get('model')}) and differ only by `thinking` "
                f"({a.get('thinking')} vs {b.get('thinking')}). Measured on one non-Anthropic "
                f"provider (2026-09-24), thinking moved nothing outside the noise — so a chain "
                f"holding both may cost the same and deliver the same at each step"
                + (f"; chained together by: {', '.join(using)}" if using else "")
                + ". Give one of them a different model, or drop it."
            )
    return out


def _all_chain_members(spec: dict) -> set[str]:
    """Every strength an activity can reach, at any complexity."""
    from .resolve import COMPLEXITIES

    out: set[str] = set(spec.get("strengths") or [])
    for label in COMPLEXITIES:
        out |= set(((spec.get(label) or {}).get("strengths")) or [])
    return out


def _unenforced_budgets(cfg: dict) -> list[str]:
    """Every (activity, complexity) that resolves no ceiling or no told budget.

    AT CONFIG TIME, because a warning that arrives after the money is spent is not a
    warning. Both are optional by design — the operator may decline a ceiling — but an
    undeclared one must be visible, or "no ceiling" and "a ceiling nothing can check" become
    indistinguishable in the records.
    """
    from .resolve import COMPLEXITIES, resolve_field

    # ONLY THE CEILING WARNS, and once per activity. A missing `task_budget_tokens` is the
    # shipped norm for every reading activity — under tiers only `worker` carried one — so
    # warning on it would fire 30 times on the plugin's own config, and a check that cries
    # wolf gets disabled and then catches nothing. A missing CEILING is different: it was
    # mandatory on every tier, so its absence is new, and it is the difference between a
    # bounded dispatch and an unbounded one.
    out: list[str] = []
    for aid, spec in sorted((cfg.get("activities") or {}).items()):
        uncapped = [lb for lb in COMPLEXITIES if resolve_field(spec, lb, "max_budget_usd") is None]
        if uncapped:
            where = "every complexity" if len(uncapped) == len(COMPLEXITIES) else ", ".join(uncapped)
            out.append(
                f"activity {aid} declares no `max_budget_usd` at {where} — nothing will bound a "
                f"dispatch's spend, and its record will read `ceiling_source: unset`. Deliberate "
                f"is fine; silent is not"
            )
    return out


def _unused_strengths(cfg: dict) -> list[str]:
    """A strength no activity can reach — dead config, or a typo in a chain.

    This replaces the ladder's "every tier must be placed" HARD error with a warning, which
    is the right severity: `--strength` for a deliberate experiment is a legitimate use.
    """
    reachable: set[str] = set()
    for spec in (cfg.get("activities") or {}).values():
        reachable |= _all_chain_members(spec)
    return [f"strength {name!r} is reachable from no activity — dead, or a typo in a chain"
            for name in sorted(set(cfg.get("strengths") or {}) - reachable)]

def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    strict = "--strict" in args
    if "--stamp" in args:
        installed = plugin_version()
        try:
            print(stamp(PROJECT_FILE, installed))
        except OSError as exc:
            print(f"FAIL: cannot stamp {PROJECT_FILE}: {exc}", file=sys.stderr)
            return 2
        args.remove("--stamp")
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

    # WHAT THE PROJECT PATCHED, said out loud. A project may patch any strength or activity
    # — including what a security surface routes to — and the protection is these rows, not a
    # refusal: an operator reading the check sees exactly what now runs on what, beside what
    # the plugin ships. There is no `agent_tiers:` row any more because there is no such
    # block: a project patches `activities:` itself, which is what these lines report.
    try:
        from .resolve import COMPLEXITIES, load_config, provenance

        cfg = load_config()
        prov = provenance(cfg)
        for name in prov["strengths"]:
            spec = cfg["strengths"][name]
            shipped = prov["shipped"].get(name)
            was = f"plugin ships {shipped['provider']}/{shipped['model']}" if shipped else "a new strength; the plugin ships none"
            print(f"models:  strength {name} = {spec['provider']}/{spec['model']} thinking={spec['thinking']}  (project patch; {was})")
        for aid in prov["activities"]:
            spec = cfg["activities"][aid]
            chains = [f"{lb}:[{', '.join((spec.get(lb) or {}).get('strengths') or [])}]"
                      for lb in COMPLEXITIES if (spec.get(lb) or {}).get("strengths")]
            top = f"[{', '.join(spec.get('strengths') or [])}]" if spec.get("strengths") else "—"
            print(f"models:  activity {aid} ({spec.get('agent')}) = {top} {' '.join(chains)}  (project patch)")
        for name in prov["providers"]:
            print(f"models:  provider {name} = env [{', '.join(sorted((cfg['providers'][name].get('env') or {})))}]  (project {'patch' if name in ('anthropic', 'deepseek') else 'addition'})")
        # A VERIFICATION ACTIVITY ROUTED OFF ANTHROPIC is visibility, not prevention — the
        # same posture `merge_model_config` states. A cheaper lens is a legitimate experiment
        # and a measured one; it should simply never be invisible.
        for aid, spec in sorted((cfg.get("activities") or {}).items()):
            if not aid.startswith("verify."):
                continue
            for name in sorted(_all_chain_members(spec)):
                sspec = (cfg.get("strengths") or {}).get(name) or {}
                if sspec.get("provider") not in (None, "anthropic"):
                    warnings.append(
                        f"{aid} can run at strength {name!r} on {sspec['provider']}/{sspec['model']} "
                        f"— a verification lens off Anthropic. Allowed, and worth knowing: measure "
                        f"its catch rate with `ab.sh … --lenses` before trusting it"
                    )
        warnings.extend(_collapsed_strengths(cfg))
        warnings.extend(_unused_strengths(cfg))
        warnings.extend(_unenforced_budgets(cfg))
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
