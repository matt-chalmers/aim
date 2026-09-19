"""mutate.sh keeps its trees inside the checkout by default. Under /tmp a sandboxed worker
could not read its own mutations file back (measured: "Path is outside allowed working
directories"), and "with SCRATCHPAD set" had workers prefixing the call with
`env SCRATCHPAD=…`, which no rule matches."""

from __future__ import annotations

import re

from models.resolve import HARNESS

SCRIPT = HARNESS / "verify" / "mutate.sh"


def test_the_scratch_root_defaults_inside_the_checkout_and_scratchpad_still_overrides():
    text = SCRIPT.read_text()
    m = re.search(r'^ROOT="(.+)"$', text, re.M)
    assert m, "mutate.sh no longer sets ROOT on one line"
    assert m.group(1) == "${SCRATCHPAD:-$PWD/.harness/run/mut}/${SLUG}-mut", m.group(1)


def test_the_doctrine_no_longer_asks_for_an_environment_prefix():
    from models.resolve import _prompts_dir

    doctrine = (_prompts_dir("skills") / "test-doctrine" / "SKILL.md").read_text()
    assert "with\n`SCRATCHPAD`" not in doctrine and "with `SCRATCHPAD`" not in doctrine
    assert "no environment prefix" in doctrine
