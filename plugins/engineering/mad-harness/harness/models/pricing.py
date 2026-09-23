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

THE CEILING IS ENFORCED AGAINST THE SAME NUMBER, since 0.10.34. `--max-budget-usd` is
checked by the CLI against its own estimate, so on a provider it prices at 4-8x the ceiling
bit that much sooner than the figure said — a worker killed a fifth of the way into its
task, looking like the model failing. `Meter` below is the fix: `dispatch.py` already
streams the agent's messages and each one carries its own `usage`, so the real cost is
added up as it goes and the dispatch is stopped when IT passes the ceiling. The CLI's own
ceiling is still passed underneath, raised by `resolve.CLI_BACKSTOP_FACTOR`, so it can only
fire after this one should have.

WHAT A METERED CEILING STILL IS NOT. It is checked between turns, so a single enormous tool
call can carry a dispatch past it — the same property the CLI's has (measured: a $0.005
ceiling produced a $0.1118 dispatch, 22x). It stops a runaway loop; it does not bound one
large call, and `task_budget_tokens` remains the thing that paces the agent from inside.

PEAK AND OFF-PEAK ARE DATA, NOT A CONSTANT. DeepSeek charges half rate outside 01:00-04:00
and 06:00-10:00 UTC on weekdays; assuming one or the other is a 2x error in either
direction, so the windows are declared in the price block and the rate is chosen by the
dispatch's own start time.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any

#: Every key a price block may carry. Rates are US dollars per million tokens.
KEYS = frozenset({
    "input_per_mtok", "output_per_mtok", "cache_read_per_mtok", "cache_write_per_mtok",
    "off_peak_multiplier", "peak_utc", "peak_weekdays_only",
})
REQUIRED = ("input_per_mtok", "output_per_mtok")
#: The four token classes, and every spelling a usage payload reports them under.
_USAGE_KEYS: dict[str, tuple[str, ...]] = {
    "input_tokens": ("input_tokens",),
    "output_tokens": ("output_tokens",),
    "cache_read_tokens": ("cache_read_tokens", "cache_read_input_tokens"),
    "cache_creation_tokens": ("cache_creation_tokens", "cache_creation_input_tokens"),
}


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


@dataclass
class Meter:
    """A dispatch's real cost as it streams, and whether it has passed its ceiling.

    WHY THIS EXISTS RATHER THAN A CONVERSION FACTOR. The other way to make the ceiling
    mean dollars on a third-party provider is to hand the CLI a multiple of it — the CLI
    over-prices DeepSeek by a factor we have measured. But that factor is a property of a
    Claude Code RELEASE, not a contract: if its fallback table changes, every ceiling
    silently moves and nothing says so. Adding up the tokens we are already given, at the
    rates the tier declares, is enforcement against the number that ends up in the record.

    ONE API RESPONSE, SEVERAL MESSAGES, ONE USAGE. Measured against a live DeepSeek stream
    (2026-09-23): each response arrives as one `AssistantMessage` PER CONTENT BLOCK — a
    thinking block, then a tool-use block — and every one of them carries the SAME usage
    payload. Adding them up as they arrive double-counts. Deduplicated on `message_id` the
    prompt totals are exact: 12,769 + 197 + 160 + 104 + 148 = 13,378 input and
    0 + 12,928 + 13,184 + 13,440 + 13,568 = 53,120 cache-read, against the result message's
    13,378 and 53,120. So `add` takes the id and ignores a repeat.

    OUTPUT TOKENS ARE NOT IN A STREAMED USAGE. Every one of those ten messages reported
    `output_tokens: 0`; the true 682 appeared only in the result message at the end. So
    this prices PROMPT TOKENS ONLY, and the ceiling it enforces is therefore reached a
    little late — never early. On that sample the output was $0.0027 of a $0.0227 dispatch,
    11.9%. That is the bias, measured and named; the alternative was to estimate output
    from the text we can see, which is a plausible number of exactly the kind this module
    exists to keep out of the record.

    A message whose `usage` is absent or empty is not counted, and `turns_metered` stays
    at zero — so a provider that reports nothing reads as UNENFORCED rather than as free.
    """

    price: dict[str, Any]
    ceiling: float
    when: _dt.datetime | None = None
    totals: dict[str, int] = field(default_factory=lambda: dict.fromkeys(_USAGE_KEYS, 0))
    turns_metered: int = 0
    seen: set[str] = field(default_factory=set)

    def add(self, usage: dict[str, Any] | None, message_id: str | None = None) -> None:
        """Fold one API response's usage in, once. The API spells cache counts
        `cache_read_input_tokens` and `cache_creation_input_tokens`; the SDK result spells
        them without `input`. A repeated `message_id` is the same response arriving again
        as another content block, and is ignored."""
        if not usage:
            return
        if message_id is not None:
            if message_id in self.seen:
                return
            self.seen.add(message_id)
        counted = False
        for key, spellings in _USAGE_KEYS.items():
            for spelling in spellings:
                if usage.get(spelling) is not None:
                    self.totals[key] += int(usage[spelling] or 0)
                    counted = True
                    break
        if counted:
            self.turns_metered += 1

    def spent(self) -> tuple[float, str]:
        """(dollars so far, how it was reached) — prompt tokens only, see above."""
        usd, how = cost(self.price, self.totals, self.when)
        return usd, how.replace("priced (", "priced (prompt only, ", 1)

    def over(self) -> tuple[float, str] | None:
        """The spend and its derivation once it has passed the ceiling, else None."""
        if not self.ceiling or not self.turns_metered:
            return None
        usd, how = self.spent()
        return (usd, how) if usd >= self.ceiling else None
