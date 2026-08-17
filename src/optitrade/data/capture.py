"""Capture pipeline: raw chains in, clean :class:`MarketSnapshot` out.

Also defines the :class:`CaptureSource` protocol every chain provider must
satisfy, and :class:`SyntheticSource`, a deterministic seeded implementation
used for tests and demos.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Protocol

from optitrade.core.types import MarketSnapshot, OptionQuote, OptionType
from optitrade.data.models import RawChain, RawQuote
from optitrade.data.quote_filters import DEFAULT_FILTER_CONFIG, FilterConfig, filter_chain

MIN_CLEAN_QUOTES = 4  # fewer than this cannot anchor a smile, let alone a surface


def to_market_snapshot(
    chain: RawChain, config: FilterConfig = DEFAULT_FILTER_CONFIG
) -> MarketSnapshot:
    """Filter ``chain`` and map the surviving quotes into a ``MarketSnapshot``.

    Clean quotes keep their bid/ask and get ``mid = (bid + ask) / 2``. Raises
    ``ValueError`` when fewer than ``MIN_CLEAN_QUOTES`` quotes survive, so a
    junk chain can never seed the vol surface silently.
    """
    result = filter_chain(chain, config)
    if len(result.clean) < MIN_CLEAN_QUOTES:
        raise ValueError(
            f"only {len(result.clean)} of {len(chain.quotes)} quotes survived filtering for "
            f"{chain.underlying}; need at least {MIN_CLEAN_QUOTES} to build a MarketSnapshot "
            f"(stats: {result.stats})"
        )
    quotes = tuple(
        OptionQuote(
            strike=q.strike,
            expiry=q.expiry,
            option_type=q.option_type,
            mid=0.5 * (q.bid + q.ask),
            bid=q.bid,
            ask=q.ask,
        )
        for q in result.clean
    )
    return MarketSnapshot(
        spot=chain.spot,
        rate=chain.rate,
        timestamp=chain.timestamp,
        quotes=quotes,
        dividend_yield=chain.dividend_yield,
    )


class CaptureSource(Protocol):
    """Anything that can fetch a raw option chain for an underlying."""

    def fetch_chain(self, underlying: str) -> RawChain:
        """Return the current raw chain for ``underlying``."""
        ...


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _black_scholes_mid(
    spot: float,
    strike: float,
    expiry: float,
    rate: float,
    dividend_yield: float,
    vol: float,
    option_type: OptionType,
) -> float:
    sqrt_t = math.sqrt(expiry)
    d1 = (math.log(spot / strike) + (rate - dividend_yield + 0.5 * vol * vol) * expiry) / (
        vol * sqrt_t
    )
    d2 = d1 - vol * sqrt_t
    disc_r = math.exp(-rate * expiry)
    disc_q = math.exp(-dividend_yield * expiry)
    if option_type is OptionType.CALL:
        return spot * disc_q * _norm_cdf(d1) - strike * disc_r * _norm_cdf(d2)
    return strike * disc_r * _norm_cdf(-d2) - spot * disc_q * _norm_cdf(-d1)


@dataclass(frozen=True)
class SyntheticSource(CaptureSource):
    """Deterministic synthetic NSE-style option chain source.

    Generates a seeded, plausible chain: a vol smile with put skew, tight books
    and healthy volume/OI near the money, and progressively wider, one-sided,
    staler quotes in the wings — so the hygiene filters in
    :mod:`optitrade.data.quote_filters` have realistic work to do while the
    near-ATM quotes always survive cleaning.

    This is the reference implementation of the :class:`CaptureSource` protocol:
    the Upstox adapter in ``options_trading`` follows the same contract
    (``fetch_chain(underlying) -> RawChain``), swapping this generator for
    broker API calls. Fully deterministic given ``seed`` and the underlying
    name — no network, no wall clock.
    """

    seed: int = 7
    spot: float = 24_500.0
    rate: float = 0.065
    dividend_yield: float = 0.0
    timestamp: float = 1_755_500_000.0  # fixed epoch second; callers override for replay tests
    strikes_per_side: int = 6
    strike_step: float = 100.0
    expiries: tuple[float, ...] = (7.0 / 365.0, 28.0 / 365.0)

    # Shape parameters for the generated market (kept here, not inline magic).
    _tick: float = 0.05
    _base_vol: float = 0.13
    _liquidity_width: float = 0.012  # log-moneyness scale over which liquidity decays

    def fetch_chain(self, underlying: str) -> RawChain:
        rng = random.Random(f"{self.seed}:{underlying}")
        atm = self.strike_step * round(self.spot / self.strike_step)
        strikes = [
            atm + i * self.strike_step
            for i in range(-self.strikes_per_side, self.strikes_per_side + 1)
        ]
        quotes: list[RawQuote] = []
        for expiry in self.expiries:
            for strike in strikes:
                log_moneyness = math.log(strike / self.spot)
                vol = (
                    self._base_vol
                    + 1.5 * log_moneyness * log_moneyness
                    + max(0.0, -0.4 * log_moneyness)  # put skew
                )
                liquidity = math.exp(-0.5 * (log_moneyness / self._liquidity_width) ** 2)
                for option_type in (OptionType.CALL, OptionType.PUT):
                    quotes.append(
                        self._make_quote(rng, strike, expiry, option_type, vol, liquidity)
                    )
        return RawChain(
            underlying=underlying,
            spot=self.spot,
            rate=self.rate,
            timestamp=self.timestamp,
            quotes=tuple(quotes),
            dividend_yield=self.dividend_yield,
        )

    def _make_quote(
        self,
        rng: random.Random,
        strike: float,
        expiry: float,
        option_type: OptionType,
        vol: float,
        liquidity: float,
    ) -> RawQuote:
        fair = _black_scholes_mid(
            self.spot, strike, expiry, self.rate, self.dividend_yield, vol, option_type
        )
        half_spread_frac = 0.008 + 0.10 * (1.0 - liquidity) + rng.uniform(0.0, 0.004)
        bid = math.floor(fair * (1.0 - half_spread_frac) / self._tick) * self._tick
        ask = math.ceil(fair * (1.0 + half_spread_frac) / self._tick) * self._tick
        illiquid = liquidity < 0.2
        if illiquid and rng.random() < 0.5:
            bid = 0.0  # one-sided wing book
        if bid < self._tick:
            bid = 0.0  # feeds report sub-tick bids as absent
        ask = max(ask, self._tick)
        volume = int(40_000 * liquidity * rng.uniform(0.3, 1.2))
        open_interest = int(400_000 * liquidity * rng.uniform(0.5, 1.5))
        if illiquid and rng.random() < 0.3:
            volume = 0
            open_interest = 0
        if volume > 0:
            ltp = round(fair * rng.uniform(0.99, 1.01) / self._tick) * self._tick
            ltp_age = rng.uniform(1.0, 90.0) if liquidity > 0.5 else rng.uniform(120.0, 3000.0)
        else:
            ltp = 0.0
            ltp_age = rng.uniform(1800.0, 20000.0)
        bid_qty = int(1500 * liquidity * rng.uniform(0.2, 1.0)) if bid > 0.0 else 0
        ask_qty = int(1500 * liquidity * rng.uniform(0.2, 1.0)) if ask > 0.0 else 0
        return RawQuote(
            strike=strike,
            expiry=expiry,
            option_type=option_type,
            bid=bid,
            ask=ask,
            ltp=ltp,
            volume=volume,
            open_interest=open_interest,
            bid_qty=bid_qty,
            ask_qty=ask_qty,
            ltp_age_seconds=ltp_age,
        )


__all__ = ["MIN_CLEAN_QUOTES", "CaptureSource", "SyntheticSource", "to_market_snapshot"]
