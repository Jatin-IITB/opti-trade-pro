"""Raw option-chain models as delivered by a market-data feed.

``RawQuote`` and ``RawChain`` deliberately mirror what NSE-style feeds provide
(bid/ask/LTP/volume/OI per contract) before any hygiene filtering. A value of
0.0 in a float field means "absent" — feeds report missing book sides and
untraded contracts as zeros.

Cleaning happens in :mod:`optitrade.data.quote_filters`; conversion to the
quant core's :class:`~optitrade.core.types.MarketSnapshot` happens in
:mod:`optitrade.data.capture`; persistence in
:mod:`optitrade.data.snapshot_store`.
"""

from __future__ import annotations

from dataclasses import dataclass

from optitrade.core.types import OptionType


@dataclass(frozen=True, slots=True)
class RawQuote:
    """One unfiltered option quote straight from the feed.

    Float fields may be 0.0 meaning absent (e.g. a one-sided wing book carries
    bid 0.0; an untraded contract carries ltp 0.0).
    """

    strike: float
    expiry: float  # year fraction (ACT/365), per ADR-003
    option_type: OptionType
    bid: float
    ask: float
    ltp: float  # last traded price
    volume: int
    open_interest: int
    bid_qty: int = 0
    ask_qty: int = 0
    ltp_age_seconds: float = 0.0  # seconds since the last trade


@dataclass(frozen=True)
class RawChain:
    """A full raw option chain for one underlying at one instant."""

    underlying: str
    spot: float
    rate: float  # continuously compounded risk-free rate
    timestamp: float  # unix epoch seconds (UTC)
    quotes: tuple[RawQuote, ...]
    dividend_yield: float = 0.0


__all__ = ["RawChain", "RawQuote"]
