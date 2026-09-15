"""`check-blocking-prose.sh` — a blocking claim in prose with no dependency edge.

Its own comments name the failure it must avoid: a lint that cries wolf gets ignored. The
subject guard existed for "<id> is blocked on <other>" inside a third record's notes, but
only when the subject was a literal id. A noun-phrase subject — "a separate sibling bead is
blocked on X" — slipped through to the generic pass and credited the containing record,
which had said two sentences earlier that it was shippable.
"""

from __future__ import annotations

from tracker import check_blocking
from tracker.port import Task


class Store:
    def __init__(self, *tasks: Task):
        self.tasks = list(tasks)

    def list(self, **kw):
        return self.tasks

    def ready(self, **kw):
        return [t for t in self.tasks if t.status == "open" and not t.depends_on]


def _t(tid, text, **kw):
    return Task(id=tid, type="task", status="open", title=tid, description=text, **kw)


REAL_TEXT = (
    "This bead is the destructive-delete block ONLY and is shippable now. The retention "
    "shape (SET_NULL vs anonymise-and-keep for published competitions) is a separate "
    "sibling bead blocked on PROJ-make Q1/Q9 and will be filed once those settle."
)


def test_a_noun_phrase_subject_is_not_credited_to_the_containing_record():
    store = Store(_t("PROJ-0o1v.6", REAL_TEXT), _t("PROJ-make", "the decision"))
    assert check_blocking.findings(store) == []


def test_the_guard_does_not_swallow_a_claim_the_record_makes_about_itself():
    """Widening the mask must not widen it to real findings."""
    store = Store(_t("PROJ-a1", "Blocked on PROJ-b2 until the schema lands."), _t("PROJ-b2", "schema"))
    found = check_blocking.findings(store)
    assert [(f[0], f[3], f[4]) for f in found] == [("PROJ-a1", "PROJ-a1", "PROJ-b2")]


def test_an_id_subject_in_a_third_record_is_still_masked():
    store = Store(
        _t("PROJ-c3", "Context: PROJ-a1 (the API half) is blocked on PROJ-b2. This one is free."),
        _t("PROJ-a1", "api", depends_on=("PROJ-b2",)),
        _t("PROJ-b2", "schema"),
    )
    assert check_blocking.findings(store) == []
