"""The lean catalog against the real CLI — no API call, one SDK connect.

The CLI stores the `skills` list unvalidated and lists the INTERSECTION with what it
discovered, so an entry that stops matching shrinks the catalog silently; the only
symptom is `not in this session's skills allowlist` at invocation, which is not a
permission denial and never reaches the dispatcher. The SDK is lock-pinned
(uv.lock: claude-agent-sdk 0.2.152, bundled CLI 2.1.259) but declared `>=`, so a
`uv lock --upgrade` moves the CLI underneath the allowlist semantics. This is the guard.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.conformance


def test_the_cli_lists_exactly_the_catalog_the_dispatcher_names(monkeypatch):
    import anyio
    from claude_agent_sdk import ClaudeSDKClient

    from models.dispatch import build_env
    from models.resolve import PLUGIN_ROOT, catalog_skills, resolve

    monkeypatch.delenv("MAD_HARNESS_LEAN_CATALOG", raising=False)
    monkeypatch.setattr("models.levers._project_block", lambda: {})
    r = resolve("fullstack-engineer")
    opts = r.sdk_options(cwd=str(PLUGIN_ROOT), env=build_env(r))
    expected = catalog_skills()
    assert opts.skills == expected

    async def listing():
        async with ClaudeSDKClient(options=opts) as client:
            return (await client.get_context_usage())["skills"]

    seen = anyio.run(listing)
    names = sorted(s["name"] for s in seen["skillFrontmatter"])
    assert seen["includedSkills"] == len(expected), (
        f"the CLI lists {seen['includedSkills']} skills for a catalog of {len(expected)} — "
        f"an entry no longer matches, and its skill would be rejected silently at invocation"
    )
    assert names == sorted(expected)
    assert not [n for n in names if n.split(":")[-1] in ("swarm", "halt", "campaign", "campaign-auto")]
