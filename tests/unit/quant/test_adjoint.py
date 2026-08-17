"""Tape/Var reverse-mode AD unit tests and bs_price_adjoint vs analytic BS."""

from __future__ import annotations

import math

import pytest

from optitrade.core import OptionType
from optitrade.greeks import Tape, bs_price_adjoint
from optitrade.greeks.adjoint import exp, log, norm_cdf, norm_pdf, sqrt
from optitrade.pricing import bs_greeks_at, bs_price


def _phi(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


@pytest.mark.unit
def test_tape_composite_exp_and_norm_cdf() -> None:
    """f(x, y) = x e^y + Phi(x y); hand-derived partials.

    df/dx = e^y + y phi(xy),  df/dy = x e^y + x phi(xy).
    """
    x, y = 0.7, -1.3
    tape = Tape()
    vx, vy = tape.var(x), tape.var(y)
    f = vx * exp(vy) + norm_cdf(vx * vy)
    tape.backward(f)

    assert f.value == pytest.approx(x * math.exp(y) + _cdf(x * y), rel=1e-14)
    assert vx.adjoint == pytest.approx(math.exp(y) + y * _phi(x * y), rel=1e-12)
    assert vy.adjoint == pytest.approx(x * math.exp(y) + x * _phi(x * y), rel=1e-12)


@pytest.mark.unit
def test_tape_log_sqrt_div_pow_neg_pdf() -> None:
    """g(x, y) = ln(x) sqrt(y) - x/y + x^3 - y phi(x); hand-derived partials."""
    x, y = 2.5, 0.8
    tape = Tape()
    vx, vy = tape.var(x), tape.var(y)
    g = log(vx) * sqrt(vy) - vx / vy + vx**3.0 + (-vy) * norm_pdf(vx)
    tape.backward(g)

    expected_value = math.log(x) * math.sqrt(y) - x / y + x**3 - y * _phi(x)
    dg_dx = math.sqrt(y) / x - 1.0 / y + 3.0 * x**2 + x * y * _phi(x)
    dg_dy = math.log(x) * 0.5 / math.sqrt(y) + x / y**2 - _phi(x)

    assert g.value == pytest.approx(expected_value, rel=1e-14)
    assert vx.adjoint == pytest.approx(dg_dx, rel=1e-12)
    assert vy.adjoint == pytest.approx(dg_dy, rel=1e-12)


@pytest.mark.unit
def test_tape_scalar_mixing_and_fan_out() -> None:
    """Float-Var mixed arithmetic plus a variable reused in several branches."""
    x = 1.7
    tape = Tape()
    vx = tape.var(x)
    f = 2.0 * vx + (3.0 - vx) / 2.0 + 1.0 / vx - vx**2.0
    tape.backward(f)
    df_dx = 2.0 - 0.5 - 1.0 / x**2 - 2.0 * x
    assert vx.adjoint == pytest.approx(df_dx, rel=1e-12)


@pytest.mark.unit
def test_backward_resets_adjoints_between_sweeps() -> None:
    tape = Tape()
    vx, vy = tape.var(2.0), tape.var(5.0)
    z = vx * vy
    w = z + vx
    tape.backward(z)
    assert vx.adjoint == pytest.approx(5.0)
    tape.backward(w)  # must not accumulate on top of the previous sweep
    assert vx.adjoint == pytest.approx(6.0)
    assert vy.adjoint == pytest.approx(2.0)


@pytest.mark.unit
@pytest.mark.parametrize("dividend_yield", [0.0, 0.03])
@pytest.mark.parametrize("option_type", [OptionType.CALL, OptionType.PUT])
@pytest.mark.parametrize(
    ("spot", "expiry", "vol"),
    [(90.0, 0.25, 0.3), (100.0, 1.0, 0.2), (120.0, 0.05, 0.15)],
)
def test_bs_adjoint_matches_analytic(
    spot: float, expiry: float, vol: float, option_type: OptionType, dividend_yield: float
) -> None:
    strike, rate = 100.0, 0.05
    price, greeks = bs_price_adjoint(spot, strike, expiry, rate, vol, option_type, dividend_yield)

    expected_price = float(bs_price(spot, strike, expiry, rate, vol, option_type, dividend_yield))
    assert price == pytest.approx(expected_price, rel=1e-9, abs=1e-12)

    ref = bs_greeks_at(spot, strike, expiry, rate, vol, option_type, dividend_yield)
    # First order comes straight off the tape: exact up to erfc-vs-ndtr ulps.
    assert greeks.delta == pytest.approx(ref.delta, rel=1e-8, abs=1e-10)
    assert greeks.vega == pytest.approx(ref.vega, rel=1e-8, abs=1e-10)
    assert greeks.rho == pytest.approx(ref.rho, rel=1e-8, abs=1e-10)
    assert greeks.theta == pytest.approx(ref.theta, rel=1e-8, abs=1e-10)
    # Second order is FD-of-AD: O(h^2) truncation with h = 1e-5.
    assert greeks.gamma == pytest.approx(ref.gamma, rel=1e-5, abs=1e-7)
    assert greeks.vanna == pytest.approx(ref.vanna, rel=1e-5, abs=1e-7)
    assert greeks.volga == pytest.approx(ref.volga, rel=1e-5, abs=1e-7)


@pytest.mark.unit
def test_adjoint_theta_sign_convention() -> None:
    """theta = -dP/dtau must be negative for a long ATM call (time decay)."""
    _, greeks = bs_price_adjoint(100.0, 100.0, 0.5, 0.03, 0.2, OptionType.CALL)
    assert greeks.theta < 0.0
