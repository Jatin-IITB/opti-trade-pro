"""Bridge between the capture layer's RawChain and the analytics API's ChainIn.

Reuses the existing ``to_market_snapshot`` filter pipeline so that the analytics
routes and the live dashboard pipeline apply identical quote hygiene. Lives in
``options_trading`` because it depends on the Pydantic models from the analytics
routes — the one-way dependency direction (ADR-002) is preserved.
"""

from __future__ import annotations

from optitrade.data.capture import to_market_snapshot
from optitrade.data.models import RawChain
from optitrade.data.quote_filters import DEFAULT_FILTER_CONFIG, FilterConfig

from ..api.routes.analytics import ChainIn, QuoteIn


def raw_chain_to_chain_in(
    chain: RawChain,
    config: FilterConfig = DEFAULT_FILTER_CONFIG,
) -> ChainIn:
    """Convert a ``RawChain`` into a ``ChainIn`` suitable for the analytics API.

    Filters the raw chain via ``to_market_snapshot`` (same pipeline the capture
    service uses), then maps each surviving ``OptionQuote`` to a ``QuoteIn``.
    """
    snapshot = to_market_snapshot(chain, config)
    return ChainIn(
        spot=snapshot.spot,
        rate=snapshot.rate,
        dividend_yield=snapshot.dividend_yield,
        quotes=[
            QuoteIn(
                strike=q.strike,
                expiry=q.expiry,
                option_type=q.option_type.value,
                mid=q.mid,
            )
            for q in snapshot.quotes
        ],
    )


__all__ = ["raw_chain_to_chain_in"]
