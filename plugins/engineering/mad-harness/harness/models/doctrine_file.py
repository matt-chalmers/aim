"""An agent's doctrine as a file — for a teammate, which loads none of it itself.

The boundary puts every skill an agent declares into its system prompt
(`resolve.doctrine`), and refuses to start when one is missing: doctrine that cannot be
delivered is not optional. An agent-teams teammate spawned from the same definition loads
its `tools` and `model` but NOT its `skills` (the docs say so), so the lead hands it the
same text by path — the one channel a teammate has — and the spawn prompt says "read this
first". This writes exactly what the dispatcher would have injected, nothing rephrased.

    doctrine.sh <agent>        → prints the path of .harness/run/doctrine/<agent>.md
"""

from __future__ import annotations

import sys
from pathlib import Path

from .resolve import AGENTS_DIR, REPO, ConfigError, doctrine

OUT = REPO / ".harness" / "run" / "doctrine"


def write(agent: str, agents_dir: Path | None = None, out_dir: Path | None = None) -> Path:
    agents_dir = agents_dir or AGENTS_DIR
    if not (agents_dir / f"{agent}.md").is_file():
        raise ConfigError(f"{agent!r} is not one of this plugin's agents ({', '.join(sorted(p.stem for p in agents_dir.glob('*.md')))})")
    text = doctrine(agent, agents_dir)
    out = (out_dir or OUT) / f"{agent}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    header = (f"# Doctrine for `{agent}` — the skills its definition declares, verbatim\n\n"
              f"This is the text the dispatcher puts in this agent's system prompt. A teammate does not load\n"
              f"it on its own: read all of it before anything else.\n\n")
    out.write_text(header + text)
    return out


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0].startswith("-"):
        print("usage: doctrine.sh <agent>", file=sys.stderr)
        return 2
    try:
        print(write(args[0]))
    except ConfigError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
