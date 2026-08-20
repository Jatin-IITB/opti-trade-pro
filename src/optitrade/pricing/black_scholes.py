"""Vectorised Black-Scholes-Merton pricing and analytic Greeks.

All functions accept floats or numpy arrays (broadcast together) for the
numeric inputs; ``option_type`` is scalar per call — price calls and puts in
separate calls and concatenate.

Unit conventions follow ``optitrade.core.types``: vega per unit vol, rho per
unit rate, theta per year (calendar time), vols and rates as decimals.

Reference: Black & Scholes (1973); Merton (1973) for the dividend yield.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np
from scipy.stats import norm

from optitrade.core import Greeks, OptionType

ArrayLike = float | np.ndarray

# Floors keep d1/d2 finite at expiry or zero vol; prices degrade gracefully
# to (discounted) intrinsic value instead of returning NaN.
_MIN_EXPIRY = 1e-12
_MIN_VOL = 1e-12


class GreeksArrays(NamedTuple):
    """Structure-of-arrays Greeks for vectorised evaluation."""

    delta: np.ndarray
    gamma: np.ndarray
    vega: np.ndarray
    theta: np.ndarray
    rho: np.ndarray
    vanna: np.ndarray
    volga: np.ndarray


def _prepare(
    spot: ArrayLike,
    strike: ArrayLike,
    expiry: ArrayLike,
    rate: ArrayLike,
    vol: ArrayLike,
    dividend_yield: ArrayLike,
) -> tuple[np.ndarray, ...]:
    s, k, t, r, v, q = np.broadcast_arrays(
        np.asarray(spot, dtype=float),
        np.asarray(strike, dtype=float),
        np.asarray(expiry, dtype=float),
        np.asarray(rate, dtype=float),
        np.asarray(vol, dtype=float),
        np.asarray(dividend_yield, dtype=float),
    )
    t = np.maximum(t, _MIN_EXPIRY)
    v = np.maximum(v, _MIN_VOL)
    return s, k, t, r, v, q


def d1_d2(
    spot: ArrayLike,
    strike: ArrayLike,
    expiry: ArrayLike,
    rate: ArrayLike,
    vol: ArrayLike,
    dividend_yield: ArrayLike = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    s, k, t, r, v, q = _prepare(spot, strike, expiry, rate, vol, dividend_yield)
    sqrt_t = np.sqrt(t)
    d1 = (np.log(s / k) + (r - q + 0.5 * v * v) * t) / (v * sqrt_t)
    d2 = d1 - v * sqrt_t
    return d1, d2


def bs_price(
    spot: ArrayLike,
    strike: ArrayLike,
    expiry: ArrayLike,
    rate: ArrayLike,
    vol: ArrayLike,
    option_type: OptionType | str = OptionType.CALL,
    dividend_yield: ArrayLike = 0.0,
) -> ArrayLike:
    s, k, t, r, v, q = _prepare(spot, strike, expiry, rate, vol, dividend_yield)
    d1, d2 = d1_d2(s, k, t, r, v, q)
    df_r = np.exp(-r * t)
    df_q = np.exp(-q * t)
    if OptionType(option_type) is OptionType.CALL:
        price = s * df_q * norm.cdf(d1) - k * df_r * norm.cdf(d2)
    else:
        price = k * df_r * norm.cdf(-d2) - s * df_q * norm.cdf(-d1)
    return float(price) if price.ndim == 0 else price


def bs_greeks(
    spot: ArrayLike,
    strike: ArrayLike,
    expiry: ArrayLike,
    rate: ArrayLike,
    vol: ArrayLike,
    option_type: OptionType | str = OptionType.CALL,
    dividend_yield: ArrayLike = 0.0,
) -> GreeksArrays:
    s, k, t, r, v, q = _prepare(spot, strike, expiry, rate, vol, dividend_yield)
    d1, d2 = d1_d2(s, k, t, r, v, q)
    sqrt_t = np.sqrt(t)
    df_r = np.exp(-r * t)
    df_q = np.exp(-q * t)
    pdf_d1 = norm.pdf(d1)

    gamma = df_q * pdf_d1 / (s * v * sqrt_t)
    vega = s * df_q * pdf_d1 * sqrt_t
    vanna = -df_q * pdf_d1 * d2 / v
    volga = vega * d1 * d2 / v

    if OptionType(option_type) is OptionType.CALL:
        delta = df_q * norm.cdf(d1)
        theta = (
            -s * df_q * pdf_d1 * v / (2.0 * sqrt_t)
            - r * k * df_r * norm.cdf(d2)
            + q * s * df_q * norm.cdf(d1)
        )
        rho = k * t * df_r * norm.cdf(d2)
    else:
        delta = -df_q * norm.cdf(-d1)
        theta = (
            -s * df_q * pdf_d1 * v / (2.0 * sqrt_t)
            + r * k * df_r * norm.cdf(-d2)
            - q * s * df_q * norm.cdf(-d1)
        )
        rho = -k * t * df_r * norm.cdf(-d2)

    return GreeksArrays(
        delta=delta, gamma=gamma, vega=vega, theta=theta, rho=rho, vanna=vanna, volga=volga
    )


def bs_greeks_at(
    spot: float,
    strike: float,
    expiry: float,
    rate: float,
    vol: float,
    option_type: OptionType | str = OptionType.CALL,
    dividend_yield: float = 0.0,
) -> Greeks:
    """Scalar convenience wrapper returning a :class:`Greeks` dataclass."""
    g = bs_greeks(spot, strike, expiry, rate, vol, option_type, dividend_yield)
    return Greeks(
        delta=float(g.delta),
        gamma=float(g.gamma),
        vega=float(g.vega),
        theta=float(g.theta),
        rho=float(g.rho),
        vanna=float(g.vanna),
        volga=float(g.volga),
    )


__all__ = ["ArrayLike", "GreeksArrays", "bs_greeks", "bs_greeks_at", "bs_price", "d1_d2"]
