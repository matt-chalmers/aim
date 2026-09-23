"""The one parser for what a dispatched agent says it concluded.

FIVE SHAPES, NO PARSER. The verification lenses return `VERDICT: PASS | FAIL` on their
first line (`verification-gate`), the workers return `<id> · PASS|FAIL|BLOCKED|SKIPPED|
NEEDS-SERIAL-LANE · …` (`worker-protocol`), and until 0.10.21 nothing in code read either:
the orchestrator read the verdict line by eye, hand-typed `VERIFIED <sha>: L1 PASS · …`
onto the task — the one artefact `resume.py` machine-reads — and the only parser that
existed (`escalate.classify`) split the worker's line on `·` and took the FIRST token as
the verdict, while the contract puts the id first, so a `BLOCKED` return was never
recognised as one. This module is what both should have called.

A MISSING VERDICT IS NOT A PASS. `broker.py` records the measured failure: a lens that
could not read the brief written for it still returned `VERDICT: PASS`. So `parse` never
infers: a result with no `VERDICT:` line is `NONE`, and a caller that treats `NONE` as
anything but "could not judge" is the failure this exists to prevent. The word PASS in
prose ("if the tests pass…") is not a verdict either — only the labelled line is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PASS = "PASS"
FAIL = "FAIL"
NONE = "NONE"

#: `VERDICT: PASS` / `**VERDICT:** FAIL` / `## VERDICT: PASS` / `> verdict — PASS (…)`.
#: The first such line wins; the qualifier is whatever follows on the line.
#:
#: MARKDOWN AROUND THE WORD IS NOT A DIFFERENT VERDICT — and the gate treats an unparsed
#: one as NONE, which is never a PASS, so the cost is paid and the answer thrown away.
#: Measured (a lab wave, 2026-09-23): three lenses in one run opened with `## VERDICT: PASS`
#: — a heading, which this matched emphasis for but not `#` — and each was recorded as "no
#: `VERDICT:` line", ~$0.60 of judgement discarded apiece and the task blocked on lenses
#: that had in fact passed it. A heading, a blockquote and emphasis are formatting; the
#: verdict is the word after them.
VERDICT = re.compile(
    r"^[ \t]*(?:[#>]+[ \t]*)*\**[ \t]*VERDICT\**[ \t]*[:—-]?[ \t]*\**[ \t]*(?P<status>PASS|FAIL)\b\**(?P<qual>[^\n]*)",
    re.IGNORECASE | re.MULTILINE,
)
#: Findings are tagged `blocking` or `filed` (`verification-gate`); counted, not judged.
BLOCKING = re.compile(r"\bblocking\b", re.IGNORECASE)
FILED = re.compile(r"\bfiled\b", re.IGNORECASE)
#: L1 classifies a FAIL as test-shaped (route to quality-engineer) or not.
TEST_SHAPED = re.compile(r"\btest-shaped\b", re.IGNORECASE)

#: The worker return contract's status vocabulary, in the order they are looked for.
RETURN_STATUSES = ("PASS", "FAIL", "BLOCKED", "SKIPPED", "NEEDS-SERIAL-LANE")


@dataclass(frozen=True)
class Verdict:
    status: str
    qualifier: str = ""
    blocking: int = 0
    filed: int = 0
    test_shaped: bool = False

    @property
    def judged(self) -> bool:
        return self.status in (PASS, FAIL)


def parse(text: str) -> Verdict:
    """The lens verdict in `text`, or `NONE` when no `VERDICT:` line is present."""
    m = VERDICT.search(text or "")
    if not m:
        return Verdict(NONE)
    status = m.group("status").upper()
    qual = m.group("qual").strip().strip("*").strip()
    return Verdict(
        status=status,
        qualifier=qual,
        blocking=len(BLOCKING.findall(text)),
        filed=len(FILED.findall(text)),
        test_shaped=bool(TEST_SHAPED.search(text)),
    )


def return_status(text: str) -> str:
    """The status token of a worker's ten-line return — `<id> · PASS · …` — or `NONE`.

    Tolerant of the id being first (the contract) or the status being first (what the
    old parser assumed): the first `·`-separated token of the first non-empty line that
    IS a status word is the status. Nothing else on the line is read."""
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        for tok in line.split("·"):
            word = tok.strip().strip("*").upper()
            if word in RETURN_STATUSES:
                return word
        return NONE
    return NONE
