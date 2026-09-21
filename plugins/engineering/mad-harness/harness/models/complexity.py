"""How much planning an epic needs — read from what the project declares, before §3 spends.

WHY. Measured (0.10.28): §3 on a 3-task epic that already had its tasks cost 29 minutes and
$7.33 — the architect at `strategic` (Opus, max effort) deliberating ~17k tokens over three
string normalisers — against $1.07 and 3.5 minutes to build them. Thinking is spent per
turn at the tier's effort; it does not scale down with the epic on its own. So the tier is
chosen from the epic, and the epic is read here: the paths its tasks name (existing or
new, and how long), the declared `areas` they fall in and whether any carries a trigger,
the declared `security` surface, megafiles, contention edges. Every one of these is a
fact the project wrote into `harness.yaml` or the tracker; none is a threshold picked
here. A task count is shown, never gated on — three tasks that rewrite the auth adapter
are not simple.

THREE READINGS. FLAGGED: something the project marked — a path in an area with a
trigger, a security surface, a megafile, a contention edge — the declared (deepest) tier.
SIMPLE: at least one task naming paths, and nothing flagged; new files count for nothing.
UNREADABLE: no tasks, or tasks naming no paths — nothing to read the epic by. The owner's
rule (0.10.28): the architect runs at `strong` unless the surface is FLAGGED, unreadable
included, and escalates to `strategic` itself when what it reads needs it; the audit runs
at `worker` only when SIMPLE.

THE MODEL CAN ESCALATE, NEVER THE REVERSE. A stage run at a lighter tier may answer
`ADEQUACY: ESCALATE — <why>` (architect) or `VERDICT: ESCALATE — <why>` (analyst) and is
re-run once at its declared tier with the reason carried. The card sets the default; the
model that read the code overrules it upward. That is the complement of `high_risk`,
which forces a tier up on a security surface.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .resolve import HARNESS, REPO
from .steps import execute
from .wave_plan import line_count, paths_in

TK = HARNESS / "tracker" / "tk.sh"


@dataclass
class Card:
    tasks: int = 0
    existing: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)
    lines: dict[str, int] = field(default_factory=dict)
    megafiles: list[str] = field(default_factory=list)
    areas: dict[str, list[str]] = field(default_factory=dict)
    triggered: dict[str, list[str]] = field(default_factory=dict)  # area label -> paths, where the area has a trigger
    security: list[str] = field(default_factory=list)
    edges: int | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def flagged(self) -> bool:
        return bool(self.triggered or self.security or self.megafiles or (self.edges or 0))

    @property
    def unreadable(self) -> bool:
        return self.tasks == 0 or (not self.existing and not self.new)

    @property
    def simple(self) -> bool:
        return not self.unreadable and not self.flagged

    @property
    def reading(self) -> str:
        return "flagged" if self.flagged else ("unreadable" if self.unreadable else "simple")

    def why(self) -> str:
        reasons = []
        if self.triggered:
            reasons.append("touches a triggered area: " + "; ".join(f"{a} ({', '.join(ps[:3])})" for a, ps in self.triggered.items()))
        if self.security:
            reasons.append("security surface: " + "; ".join(self.security[:3]))
        if self.megafiles:
            reasons.append("megafile: " + ", ".join(f"{p} ({self.lines.get(p, 0)} lines)" for p in self.megafiles[:3]))
        if self.edges:
            reasons.append(f"{self.edges} file-contention edge(s)")
        if reasons:
            return "; ".join(reasons)
        if self.tasks == 0:
            return "no open tasks — nothing flagged, nothing to read the epic by"
        if not self.existing and not self.new:
            return "the tasks name no paths — nothing flagged, nothing to read the epic by"
        return (f"{self.tasks} task(s); {len(self.new)} new file(s), {len(self.existing)} existing ({sum(self.lines.values())} lines) "
                f"in {', '.join(self.areas) or 'no declared area'}; no triggered area, no security surface, no megafile, no contention edge")

    def render(self) -> str:
        return f"COMPLEXITY: {self.reading} — {self.why()}"


def compute(epic_text: str, children: list, project, *, cwd: str | None = None, runner=None, epic_id: str | None = None) -> Card:
    """The card, from the epic's and its children's text, the repository, and the project.
    `children` are the tracker's open child records (Task); `epic_id` enables the
    contention read (`tk.sh validate --paths`), which needs tasks in the tracker."""
    cwd = cwd or str(REPO)
    card = Card(tasks=len(children))
    texts = [epic_text or ""] + [f"{getattr(c, 'title', '')}\n{getattr(c, 'description', '')}\n{getattr(c, 'acceptance', '')}" for c in children]
    existing: list[str] = []
    new: list[str] = []
    for t in texts:
        e, n = paths_in(t, Path(cwd))
        existing += [p for p in e if p not in existing]
        new += [p for p in n if p not in new and p not in existing]
    card.existing, card.new = existing, [p for p in new if p not in existing]
    mega = project.megafile_lines() if project else None
    for p in existing:
        n = line_count(Path(cwd), p)
        card.lines[p] = n
        if mega and n > mega:
            card.megafiles.append(p)
        area = project.area_for(p) if project else None
        if area is not None:
            card.areas.setdefault(area.label, []).append(p)
            if area.triggers:
                card.triggered.setdefault(area.label, []).append(p)
    if project:
        for prefix in project.security_paths():
            hit = [p for p in existing + new if p.startswith(prefix)]
            if hit:
                card.security.append(f"security.paths `{prefix}`: {', '.join(hit[:3])}")
        blob = "\n".join(texts).lower()
        for token in project.security_tokens():
            if token.lower() in blob:
                card.security.append(f"security.tokens `{token}` named in the tasks")
    if epic_id and children:
        raw = execute([str(TK), "validate", epic_id, "--paths"], cwd=cwd, runner=runner)
        if raw.ran and raw.returncode == 0:
            try:
                doc = json.loads(raw.stdout or "{}")
                card.edges = sum(len(w.get("edges") or []) for w in ((doc.get("contention") or {}).get("waves") or []))
            except ValueError:
                card.notes.append("validate --paths did not answer in JSON; contention unread")
        else:
            card.notes.append("validate --paths not OK; contention unread")
    return card
