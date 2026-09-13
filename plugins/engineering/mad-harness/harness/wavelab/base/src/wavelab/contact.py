"""Assemble a cleaned contact record.

The final task of the wavelab epic wires the normalisers into this. It starts as a
passthrough so the suite is green before the wave runs — a base that starts red would make
every later failure ambiguous.
"""

from __future__ import annotations


def clean_contact(raw: dict) -> dict:
    """Return a cleaned copy of `raw`. Currently a passthrough."""
    return dict(raw)
