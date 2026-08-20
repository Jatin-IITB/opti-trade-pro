"""fd_greeks vs the analytic Black-Scholes ground truth, plus model-agnosticism."""

from __future__ import annotations

import math

import pytest

from optitrade.core import NumericalError, OptionType
from optitrade.greeks import FDBumps, PriceFn, fd_greeks
from optitrade.pricing import bs_greeks_at, bs_price

STRIKE = 100.0
VOL = 0.25
RATE = 0.04
DIVIDEND_YIELD = 0.015

# Instantaneous-theta comparison needs a small time bump: the default 1-day
# forward difference carries O(1/365) truncation error vs the analytic theta.
ACCURACY_BUMPS = FDBumps(abs_time=1e-6)


def _bs_closure(option_type: OptionType) -> PriceFn:
    def price(spot: float, vol: float, rate: float, expiry: float) -> float:
        return float(bs_price(spot, STRIKE, expiry, rate, vol, option_type, DIVIDEND_YIELD))

    return price


@pytest.mark.unit
@pytest.mark.parametrize("option_type", [OptionType.CALL, OptionType.PUT])
@pytest.mark.parametrize("expiry", [0.1, 0.5, 1.5])
@pytest.mark.parametrize("spot", [85.0, 100.0, 115.0])
def test_fd_matches_analytic_bs(spot: float, expiry: float, option_type: OptionType) -> None:
    fd = fd_greeks(_bs_closure(option_type), spot, VOL, RATE, expiry, bumps=ACCURACY_BUMPS)
    ref = bs_greeks_at(spot, STRIKE, expiry, RATE, VOL, option_type, DIVIDEND_YIELD)

    assert fd.delta == pytest.approx(ref.delta, rel=1e-5, abs=1e-8)
    assert fd.gamma == pytest.approx(ref.gamma, rel=1e-5, abs=1e-8)
    assert fd.vega == pytest.approx(ref.vega, rel=1e-5, abs=1e-8)
    assert fd.rho == pytest.approx(ref.rho, rel=1e-5, abs=1e-8)
    assert fd.theta == pytest.approx(ref.theta, rel=1e-4, abs=1e-8)
    assert fd.vanna == pytest.approx(ref.vanna, rel=1e-3, abs=1e-6)
    assert fd.volga == pytest.approx(ref.volga, rel=1e-3, abs=1e-6)


@pytest.mark.unit
def test_model_agnostic_toy_price_fn() -> None:
    """A non-BS polynomial pricer with hand-computed derivatives.

    P(s, v, r, tau) = s^2 v + s v^2 + r tau^2. Central differences are exact
    for polynomials of degree <= 2 per variable; the forward theta stencil has
    the exact value -2 r tau + r dt (with dt the default 1-day bump).
    """

    def toy(s: float, v: float, r: float, t: float) -> float:
        return s * s * v + s * v * v + r * t * t

    s, v, r, t = 3.0, 0.4, 0.02, 1.2
    bumps = FDBumps()
    g = fd_greeks(toy, s, v, r, t, bumps=bumps)

    assert g.delta == pytest.approx(2.0 * s * v + v * v, rel=1e-9)
    assert g.gamma == pytest.approx(2.0 * v, rel=1e-6)
    assert g.vega == pytest.approx(s * s + 2.0 * s * v, rel=1e-9)
    assert g.volga == pytest.approx(2.0 * s, rel=1e-6)
    assert g.vanna == pytest.approx(2.0 * s + 2.0 * v, rel=1e-6)
    assert g.rho == pytest.approx(t * t, rel=1e-9)
    assert g.theta == pytest.approx(-2.0 * r * t + r * bumps.abs_time, rel=1e-9)


@pytest.mark.unit
def test_theta_bump_shrinks_when_it_would_cross_expiry() -> None:
    """Default 1-day bump on a half-day option must not reprice past expiry."""
    expiry = 0.5 / 365.0
    fd = fd_greeks(_bs_closure(OptionType.CALL), 100.0, VOL, RATE, expiry)
    assert math.isfinite(fd.theta)
    assert fd.theta < 0.0  # a long ATM option decays


@pytest.mark.unit
def test_default_one_day_theta_is_close_to_analytic_for_long_expiry() -> None:
    fd = fd_greeks(_bs_closure(OptionType.CALL), 100.0, VOL, RATE, 1.0)
    ref = bs_greeks_at(100.0, STRIKE, 1.0, RATE, VOL, OptionType.CALL, DIVIDEND_YIELD)
    # 1-day forward difference: O(dt) accuracy only.
    assert fd.theta == pytest.approx(ref.theta, rel=5e-3)


@pytest.mark.unit
def test_invalid_inputs_raise_numerical_error() -> None:
    with pytest.raises(NumericalError):
        fd_greeks(_bs_closure(OptionType.CALL), -100.0, VOL, RATE, 0.5)
    with pytest.raises(NumericalError):
        fd_greeks(_bs_closure(OptionType.CALL), 100.0, VOL, RATE, 0.0)

    def nan_fn(s: float, v: float, r: float, t: float) -> float:
        return float("nan")

    with pytest.raises(NumericalError):
        fd_greeks(nan_fn, 100.0, VOL, RATE, 0.5)


@pytest.mark.unit
def test_bumps_are_overridable_and_frozen() -> None:
    bumps = FDBumps(rel_spot=1e-5, abs_vol=2e-4, abs_rate=5e-5, abs_time=1e-7)
    fd = fd_greeks(_bs_closure(OptionType.PUT), 100.0, VOL, RATE, 0.5, bumps=bumps)
    ref = bs_greeks_at(100.0, STRIKE, 0.5, RATE, VOL, OptionType.PUT, DIVIDEND_YIELD)
    assert fd.delta == pytest.approx(ref.delta, rel=1e-5)
    with pytest.raises(AttributeError):
        bumps.rel_spot = 1.0  # type: ignore[misc]
