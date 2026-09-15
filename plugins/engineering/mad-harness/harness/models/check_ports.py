"""Which of the project's declared ports are already bound, and by what.

A campaign owns every port its stacks bind — the dev servers, the end-to-end target — so
a port that is already listening when the run starts is a dev server left over from a
killed run, or another campaign on this machine, and the wave that needs it will fail
one worker at a time. This says so in one line, before any worker is dispatched.

The ports come from `harness.yaml`'s `ports:` block. NOTHING IS SCRAPED: the previous
pre-flight grepped the stack card for four-digit numbers, found none, and ran `lsof`
with no arguments — examining nothing, and reading as a pass. With no block declared
this prints that it has nothing to check, which is an answer rather than silence.

Advisory by default, because a human may legitimately have the app up while starting a
run; `--strict` turns a bound port into exit 1 for unattended use.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable

from .project import ProjectError, load

#: A probe: port -> the pids listening on it. Injectable so the logic is testable
#: without binding sockets.
Probe = Callable[[int], list[int]]


def lsof_probe(port: int) -> list[int]:
    """`lsof` is the one tool present on both macOS and Linux that answers this
    without root. `-t` prints bare pids; `-sTCP:LISTEN` excludes clients."""
    proc = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return [int(x) for x in proc.stdout.split() if x.isdigit()]


def bound(ports: dict[str, int], probe: Probe | None = None) -> dict[str, tuple[int, list[int]]]:
    """name -> (port, pids) for every declared port something is listening on."""
    probe = probe or lsof_probe  # looked up at call time, so a test can swap it
    out: dict[str, tuple[int, list[int]]] = {}
    for name, port in sorted(ports.items(), key=lambda kv: kv[1]):
        pids = probe(port)
        if pids:
            out[name] = (port, pids)
    return out


def main(argv: list[str] | None = None) -> int:
    strict = "--strict" in (argv if argv is not None else sys.argv[1:])
    try:
        p = load()
        ports = p.ports()
    except ProjectError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    print(f"project: {p.name}")
    if not ports:
        print("ports: none declared in harness.yaml — nothing to check. Declare the ports "
              "your servers bind under `ports:` and this pre-flight will examine them.")
        return 0
    if shutil.which("lsof") is None:
        print("ports: lsof is not on PATH, so the declared ports cannot be probed.",
              file=sys.stderr)
        return 2 if strict else 0

    taken = bound(ports)
    free = {k: v for k, v in ports.items() if k not in taken}
    if free:
        print("ports free: " + ", ".join(f"{k}={v}" for k, v in sorted(free.items(), key=lambda kv: kv[1])))
    if not taken:
        print(f"OK — none of the {len(ports)} declared port(s) is bound.")
        return 0
    for name, (port, pids) in taken.items():
        print(f"BOUND: {name}={port} is listening — pid(s) {', '.join(map(str, pids))}",
              file=sys.stderr)
    print(
        "A port bound before the run starts is a server left over from a killed run or "
        "another campaign on this machine; the wave that needs it will fail a worker at a "
        "time. Stop it, or run with it if it is yours on purpose.",
        file=sys.stderr,
    )
    return 1 if strict else 0


if __name__ == "__main__":
    sys.exit(main())
