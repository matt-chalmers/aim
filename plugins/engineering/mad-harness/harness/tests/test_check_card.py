"""The orchestrator card check can fail — on a missing copy, a diverged copy, and a card
over budget — and --write repairs the first two."""

from __future__ import annotations

from models import check_card as mod


def _fake_commands(tmp_path, monkeypatch, texts: dict[str, str]):
    d = tmp_path / "commands"
    d.mkdir()
    for name, text in texts.items():
        (d / f"{name}.md").write_text(text)
    monkeypatch.setattr(mod, "_prompts_dir", lambda kind: d if kind == "commands" else mod._prompts_dir(kind))
    return d


def _with_card(card_body: str, head="---\nname: x\n---\n") -> str:
    return f"{head}\n<!-- ORCHESTRATOR CARD: copy -->\n{card_body}\n<!-- END ORCHESTRATOR CARD -->\n\nbody\n"


def test_a_missing_or_diverged_copy_fails_and_write_repairs_it(tmp_path, monkeypatch):
    card = mod.canonical()
    d = _fake_commands(tmp_path, monkeypatch, {
        "good": _with_card(card),
        "stale": _with_card(card.replace("Three rules", "Two rules")),
        "bare": "---\nname: bare\n---\n\nno card here\n",
    })
    bad = mod.problems()
    assert any("stale.md" in b and "differs" in b for b in bad)
    assert any("bare.md" in b and "no ORCHESTRATOR CARD" in b for b in bad)
    assert not any("good.md" in b for b in bad)
    assert mod.main() == 1
    assert mod.write() == 0
    for name in ("good", "stale", "bare"):
        assert mod.body((d / f"{name}.md").read_text()) == card
    # The inserted copy sits after the frontmatter, and the body survives.
    text = (d / "bare.md").read_text()
    assert text.index("---\n\n<!-- ORCHESTRATOR CARD") < text.index("no card here")


def test_a_card_over_budget_fails_even_when_every_copy_agrees(tmp_path, monkeypatch):
    big = "x" * (mod.CARD_BUDGET_CHARS + 1)
    canon = tmp_path / "orchestrator-card.md"
    canon.write_text(f"<!-- ORCHESTRATOR CARD: canonical -->\n{big}\n<!-- END ORCHESTRATOR CARD -->\n")
    monkeypatch.setattr(mod, "CANONICAL", canon)
    _fake_commands(tmp_path, monkeypatch, {"only": _with_card(big)})
    assert any("budget" in b for b in mod.problems())


def test_the_real_tree_is_clean():
    assert mod.problems() == []
