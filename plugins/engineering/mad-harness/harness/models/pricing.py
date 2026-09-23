"""What a dispatch actually cost, when the CLI cannot know.

WHY. Every `harness.dispatch` event's `cost_usd` comes from the SDK's `total_cost_usd`,
which Claude Code computes from its own price table for the model name it was given. For
Anthropic that is the vendor's own accounting and it is right. Against a third-party
endpoint reached through `ANTHROPIC_BASE_URL` it is a fiction — and a plausible-looking
one, which is worse than a zero: measured three times against DeepSeek on 2026-09-23, it
came back at a flat **$5.00 per Mtok of input** (35,335 tok → $0.17675; 27,845 → $0.1393;
1,909 → $0.00962) where DeepSeek's published input price is $0.66–$1.32. That number would
have flowed into `make models-cost`, `ab-report.sh` and the `tier_source` split as if real.

SO A TIER ON A NON-ANTHROPIC PROVIDER DECLARES ITS PRICE, and the cost is computed from the
token counts — which the same provider probe certifies as real (`probe-compat.sh`'s token
accounting). `cost_source` on every event says which of the two produced the number, so a
series can never silently mix them.

THE CEILING IS A SEPARATE PROBLEM, and this does not fix it: `--max-budget-usd` is enforced
by the CLI against its own estimate, so on a provider it prices at 4-8x, the ceiling bites
4-8x sooner than the number says. A tier there should bound its work with
`task_budget_tokens`, which means what it says.

PEAK AND OFF-PEAK ARE DATA, NOT A CONSTANT. DeepSeek charges half rate outside 01:00-04:00
and 06:00-10:00 UTC on weekdays; assuming one or the other is a 2x error in either
direction, so the windows are declared in the price block and the rate is chosen by the
dispatch's own start time.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

#: Every key a price block may carry. Rates are US dollars per million tokens.
KEYS = frozenset({
    "input_per_mtok", "output_per_mtok", "cache_read_per_mtok", "cache_write_per_mtok",
    "off_peak_multiplier", "peak_utc", "peak_weekdays_only",
})
REQUIRED = ("input_per_mtok", "output_per_mtok")


def validate(price: dict[str, Any], where: str) -> dict[str, Any]:
    """The block's shape, so a typo fails the config check rather than a wave."""
    if not isinstance(price, dict):
        raise ValueError(f"{where}: price must be a map of rates per million tokens")
    unknown = set(price) - KEYS
    if unknown:
        raise ValueError(f"{where}: unknown price key(s) {', '.join(sorted(unknown))}")
    for key in REQUIRED:
        if key not in price:
            raise ValueError(f"{where}: price is missing {key!r}")
    for key in ("input_per_mtok", "output_per_mtok", "cache_read_per_mtok", "cache_write_per_mtok", "off_peak_multiplier"):
        if key in price:
            try:
                value = float(price[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{where}: price.{key} must be a number, got {price[key]!r}") from exc
            if value < 0:
                raise ValueError(f"{where}: price.{key} cannot be negative")
    for window in price.get("peak_utc") or ():
        if not isinstance(window, str) or not _parse_window(window):
            raise ValueError(f"{where}: price.peak_utc entries are 'HH:MM-HH:MM' in UTC, got {window!r}")
    return dict(price)


def _parse_window(window: str) -> tuple[int, int] | None:
    try:
        start, end = window.split("-", 1)
        sh, sm = (int(x) for x in start.split(":", 1))
        eh, em = (int(x) for x in end.split(":", 1))
    except (ValueError, AttributeError):
        return None
    if not (0 <= sh <= 24 and 0 <= eh <= 24 and 0 <= sm < 60 and 0 <= em < 60):
        return None
    return sh * 60 + sm, eh * 60 + em


def is_peak(price: dict[str, Any], when: _dt.datetime) -> bool:
    """Peak unless the block declares windows and `when` (UTC) falls outside them."""
    windows = price.get("peak_utc")
    if not windows:
        return True
    when = when.astimezone(_dt.timezone.utc)
    if price.get("peak_weekdays_only", True) and when.weekday() >= 5:
        return False
    minute = when.hour * 60 + when.minute
    for window in windows:
        parsed = _parse_window(window)
        if parsed and parsed[0] <= minute < parsed[1]:
            return True
    return False


def cost(price: dict[str, Any], usage: dict[str, Any], when: _dt.datetime | None = None) -> tuple[float, str]:
    """(dollars, how it was reached) for one dispatch's token counts.

    A cache read is billed at its own rate where the provider states one; a cache write at
    the write rate, and at the input rate where it states none — never silently free.
    """
    when = when or _dt.datetime.now(_dt.timezone.utc)
    peak = is_peak(price, when)
    factor = 1.0 if peak else float(price.get("off_peak_multiplier", 1.0))
    fresh = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    read = int(usage.get("cache_read_tokens") or usage.get("cache_read_input_tokens") or 0)
    write = int(usage.get("cache_creation_tokens") or usage.get("cache_creation_input_tokens") or 0)
    rate_in = float(price["input_per_mtok"])
    rate_out = float(price["output_per_mtok"])
    rate_read = float(price.get("cache_read_per_mtok", rate_in))
    rate_write = float(price.get("cache_write_per_mtok", rate_in))
    usd = (fresh * rate_in + out * rate_out + read * rate_read + write * rate_write) * factor / 1e6
    window = "peak" if peak else "off-peak"
    return round(usd, 6), f"priced ({window}: in ${rate_in * factor:g}/Mtok, out ${rate_out * factor:g}/Mtok, cache-read ${rate_read * factor:g}/Mtok)"
