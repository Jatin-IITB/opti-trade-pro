"""Quote hygiene filters for raw NSE option chains.

Each filter is a pure function taking a :class:`~optitrade.data.models.RawQuote`
and returning a plain-English rejection reason (with the offending numbers) or
``None`` if the quote passes. :func:`filter_chain` applies the enabled filters
in a fixed order and tags each rejected quote with the first failing filter's
reason, so results are deterministic for a given chain and config.

Thresholds live in :class:`FilterConfig`, never inline (CLAUDE.md rule 2).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from optitrade.data.models import RawChain, RawQuote


def crossed_book(quote: RawQuote) -> str | None:
    """Reject books where the bid sits above the ask (both sides present)."""
    if quote.bid > 0.0 and quote.ask > 0.0 and quote.bid > quote.ask:
        return f"crossed book: bid {quote.bid:.2f} is above ask {quote.ask:.2f}"
    return None


def stale_quote(quote: RawQuote, max_ltp_age_seconds: float = 300.0) -> str | None:
    """Reject quotes with no trading activity or a last trade older than the limit."""
    if quote.volume == 0 and quote.open_interest == 0:
        return "stale quote: zero volume and zero open interest"
    if quote.ltp_age_seconds > max_ltp_age_seconds:
        return (
            f"stale quote: last trade {quote.ltp_age_seconds:.0f}s ago exceeds the "
            f"{max_ltp_age_seconds:.0f}s limit"
        )
    return None


def wide_spread(quote: RawQuote, max_spread_frac: float = 0.25) -> str | None:
    """Reject two-sided books whose spread is too wide relative to the mid.

    Requires both sides > 0; one-sided books are judged by :func:`zero_bid_wing`
    and :func:`non_positive_mid` instead.
    """
    if quote.bid <= 0.0 or quote.ask <= 0.0:
        return None
    mid = 0.5 * (quote.bid + quote.ask)
    spread_frac = (quote.ask - quote.bid) / mid
    if spread_frac > max_spread_frac:
        return (
            f"wide spread: {spread_frac:.1%} of mid {mid:.2f} exceeds the "
            f"{max_spread_frac:.1%} limit"
        )
    return None


def zero_bid_wing(quote: RawQuote) -> str | None:
    """Reject one-sided wing quotes with no bid — the mid is unusable."""
    if quote.bid <= 0.0:
        return (
            f"zero bid wing: bid {quote.bid:.2f} against ask {quote.ask:.2f} "
            "leaves the mid unusable"
        )
    return None


def non_positive_mid(quote: RawQuote) -> str | None:
    """Reject quotes whose mid price is not strictly positive."""
    mid = 0.5 * (quote.bid + quote.ask)
    if mid <= 0.0:
        return f"non-positive mid: (bid {quote.bid:.2f} + ask {quote.ask:.2f}) / 2 = {mid:.2f}"
    return None


@dataclass(frozen=True)
class FilterConfig:
    """Thresholds and enable flags for the quote hygiene filters."""

    max_ltp_age_seconds: float = 300.0
    max_spread_frac: float = 0.25
    check_crossed_book: bool = True
    check_stale_quote: bool = True
    check_wide_spread: bool = True
    check_zero_bid_wing: bool = True
    check_non_positive_mid: bool = True


DEFAULT_FILTER_CONFIG = FilterConfig()


@dataclass(frozen=True)
class FilterResult:
    """Outcome of filtering a chain: clean quotes, rejects with reasons, counts.

    ``stats`` holds one count per filter name plus ``"clean"``; the counts sum
    to the number of quotes in the input chain.
    """

    clean: tuple[RawQuote, ...]
    rejected: tuple[tuple[RawQuote, str], ...]
    stats: dict[str, int]


def filter_chain(chain: RawChain, config: FilterConfig = DEFAULT_FILTER_CONFIG) -> FilterResult:
    """Apply the enabled filters to every quote in ``chain``, in input order.

    Each quote is rejected with the reason from the first failing filter, checked
    in this fixed order: crossed_book, stale_quote, wide_spread, zero_bid_wing,
    non_positive_mid.
    """

    def _stale(quote: RawQuote) -> str | None:
        return stale_quote(quote, max_ltp_age_seconds=config.max_ltp_age_seconds)

    def _wide(quote: RawQuote) -> str | None:
        return wide_spread(quote, max_spread_frac=config.max_spread_frac)

    checks: tuple[tuple[str, bool, Callable[[RawQuote], str | None]], ...] = (
        ("crossed_book", config.check_crossed_book, crossed_book),
        ("stale_quote", config.check_stale_quote, _stale),
        ("wide_spread", config.check_wide_spread, _wide),
        ("zero_bid_wing", config.check_zero_bid_wing, zero_bid_wing),
        ("non_positive_mid", config.check_non_positive_mid, non_positive_mid),
    )

    stats: dict[str, int] = {name: 0 for name, _, _ in checks}
    stats["clean"] = 0
    clean: list[RawQuote] = []
    rejected: list[tuple[RawQuote, str]] = []
    for quote in chain.quotes:
        reason_key: str | None = None
        reason: str | None = None
        for name, enabled, check in checks:
            if not enabled:
                continue
            reason = check(quote)
            if reason is not None:
                reason_key = name
                break
        if reason is None or reason_key is None:
            clean.append(quote)
            stats["clean"] += 1
        else:
            rejected.append((quote, reason))
            stats[reason_key] += 1
    return FilterResult(clean=tuple(clean), rejected=tuple(rejected), stats=stats)


__all__ = [
    "DEFAULT_FILTER_CONFIG",
    "FilterConfig",
    "FilterResult",
    "crossed_book",
    "filter_chain",
    "non_positive_mid",
    "stale_quote",
    "wide_spread",
    "zero_bid_wing",
]
