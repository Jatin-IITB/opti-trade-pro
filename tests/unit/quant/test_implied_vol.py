"""Implied-vol extraction: round-trips, bound validation, chain stripping."""

import math

import pytest

from optitrade.core import MarketSnapshot, NumericalError, OptionQuote, OptionType
from optitrade.pricing import bs_greeks, bs_price, implied_vol, strip_chain

SPOT, RATE, DIV = 100.0, 0.03, 0.01


@pytest.mark.parametrize("strike", [70.0, 85.0, 100.0, 115.0, 140.0])
@pytest.mark.parametrize("expiry", [0.05, 0.25, 1.0, 2.0])
@pytest.mark.parametrize("option_type", [OptionType.CALL, OptionType.PUT])
def test_round_trip_price_iv_price(strike: float, expiry: float, option_type: OptionType) -> None:
    vol_true = 0.25
    price = float(bs_price(SPOT, strike, expiry, RATE, vol_true, option_type, DIV))
    iv = implied_vol(price, SPOT, strike, expiry, RATE, option_type, dividend_yield=DIV)
    reprice = float(bs_price(SPOT, strike, expiry, RATE, iv, option_type, DIV))
    assert abs(reprice - price) < 1e-6
    # The 1e-8 price tolerance pins the vol only up to ~tol/vega, so scale the
    # iv assertion accordingly for near-vega-less deep ITM/OTM corners.
    vega = float(bs_greeks(SPOT, strike, expiry, RATE, vol_true, option_type, DIV).vega)
    assert abs(iv - vol_true) < max(1e-6, 10.0 * 1e-8 / vega)


def test_deep_otm_tiny_price_uses_bracketing_fallback() -> None:
    # Vega ~ 6e-12 at the 0.2 Newton start: the step leaves the vol bounds and
    # Brent bracketing must finish the job.
    price = float(bs_price(SPOT, 140.0, 0.05, RATE, 0.25, OptionType.CALL, DIV))
    assert price < 1e-6
    iv = implied_vol(price, SPOT, 140.0, 0.05, RATE, OptionType.CALL, dividend_yield=DIV, tol=1e-12)
    assert abs(iv - 0.25) < 1e-6


@pytest.mark.parametrize(
    ("price", "option_type"),
    [
        (200.0, OptionType.CALL),  # above S e^{-qT}
        (25.0, OptionType.CALL),  # below discounted intrinsic for K=70
        (200.0, OptionType.PUT),  # above K e^{-rT} for K=130
        (25.0, OptionType.PUT),  # below discounted intrinsic for K=130
    ],
)
def test_bound_violations_raise(price: float, option_type: OptionType) -> None:
    strike = 70.0 if option_type is OptionType.CALL else 130.0
    with pytest.raises(NumericalError, match="no-arbitrage"):
        implied_vol(price, SPOT, strike, 0.5, RATE, option_type, dividend_yield=DIV)


def test_zero_expiry_raises() -> None:
    with pytest.raises(NumericalError, match="expiry"):
        implied_vol(5.0, SPOT, 100.0, 0.0, RATE, OptionType.CALL)


def _quote(strike: float, expiry: float, vol: float, option_type: OptionType) -> OptionQuote:
    mid = float(bs_price(SPOT, strike, expiry, RATE, vol, option_type, DIV))
    return OptionQuote(strike=strike, expiry=expiry, option_type=option_type, mid=mid)


def test_strip_chain_recovers_vols_and_geometry() -> None:
    vols = {80.0: 0.28, 90.0: 0.24, 100.0: 0.21, 110.0: 0.20, 120.0: 0.22}
    quotes = tuple(
        _quote(k, 0.5, v, OptionType.CALL if k >= SPOT else OptionType.PUT) for k, v in vols.items()
    )
    snapshot = MarketSnapshot(
        spot=SPOT, rate=RATE, timestamp=0.0, quotes=quotes, dividend_yield=DIV
    )
    points = strip_chain(snapshot)
    assert len(points) == len(vols)
    forward = SPOT * math.exp((RATE - DIV) * 0.5)
    for point in points:
        assert abs(point.iv - vols[point.strike]) < 1e-7
        assert abs(point.forward - forward) < 1e-12
        assert abs(point.log_moneyness - math.log(point.strike / forward)) < 1e-12


def test_strip_chain_skips_bad_quotes() -> None:
    good = _quote(100.0, 0.5, 0.2, OptionType.CALL)
    bad = OptionQuote(strike=100.0, expiry=0.5, option_type=OptionType.CALL, mid=500.0)
    snapshot = MarketSnapshot(
        spot=SPOT, rate=RATE, timestamp=0.0, quotes=(good, bad), dividend_yield=DIV
    )
    points = strip_chain(snapshot)
    assert len(points) == 1
    assert abs(points[0].iv - 0.2) < 1e-7


def test_strip_chain_raises_when_all_quotes_fail() -> None:
    bad = OptionQuote(strike=100.0, expiry=0.5, option_type=OptionType.CALL, mid=500.0)
    snapshot = MarketSnapshot(
        spot=SPOT, rate=RATE, timestamp=0.0, quotes=(bad, bad), dividend_yield=DIV
    )
    with pytest.raises(NumericalError, match="all 2 quotes"):
        strip_chain(snapshot)
