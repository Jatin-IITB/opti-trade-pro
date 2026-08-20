"""Implied-volatility extraction from option prices.

Root-finding on the Black-Scholes-Merton price: Newton-Raphson with the
analytic vega (quadratic convergence near the root), falling back to bracketed
Brent (:func:`scipy.optimize.brentq`) when Newton stalls — vega too small to
divide by, or a step leaving the admissible vol interval.

Prices are validated against the static no-arbitrage bounds first
(Merton 1973): for a call ``max(S e^{-qT} - K e^{-rT}, 0) < C < S e^{-qT}``,
for a put ``max(K e^{-rT} - S e^{-qT}, 0) < P < K e^{-rT}``.

Conventions follow ``optitrade.core.types``: expiry is a year fraction, rates
continuously compounded, vols decimal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.optimize import brentq

from optitrade.core import MarketSnapshot, NumericalError, OptionType
from optitrade.pricing.black_scholes import bs_greeks, bs_price

_VOL_LO = 1e-9
_VOL_HI = 10.0
_MIN_VEGA = 1e-12


@dataclass(frozen=True, slots=True)
class IVPoint:
    """A single stripped implied-vol observation.

    ``log_moneyness`` is ``ln(K / F)`` with ``F = S e^{(r - q)T}`` the forward.
    """

    strike: float
    expiry: float
    iv: float
    option_type: OptionType
    forward: float
    log_moneyness: float


def _no_arb_bounds(
    spot: float,
    strike: float,
    expiry: float,
    rate: float,
    option_type: OptionType,
    dividend_yield: float,
) -> tuple[float, float]:
    df_r = math.exp(-rate * expiry)
    df_q = math.exp(-dividend_yield * expiry)
    if option_type is OptionType.CALL:
        return max(spot * df_q - strike * df_r, 0.0), spot * df_q
    return max(strike * df_r - spot * df_q, 0.0), strike * df_r


def implied_vol(
    price: float,
    spot: float,
    strike: float,
    expiry: float,
    rate: float,
    option_type: OptionType | str,
    dividend_yield: float = 0.0,
    tol: float = 1e-8,
    max_iter: int = 50,
) -> float:
    """Invert Black-Scholes for the volatility that reproduces ``price``.

    Newton-Raphson with analytic vega; falls back to Brent bracketing on
    ``[1e-9, 10]`` when Newton stalls. Raises :class:`NumericalError` if
    ``price`` violates the no-arbitrage bounds or no root is found.
    """
    opt = OptionType(option_type)
    if expiry <= 0.0:
        raise NumericalError(f"expiry must be positive to imply a vol, got {expiry}")
    lower, upper = _no_arb_bounds(spot, strike, expiry, rate, opt, dividend_yield)
    if price <= lower:
        raise NumericalError(
            f"{opt.value} price {price} violates no-arbitrage lower bound {lower:.10g} "
            f"(discounted intrinsic) for K={strike}, T={expiry}"
        )
    if price >= upper:
        raise NumericalError(
            f"{opt.value} price {price} violates no-arbitrage upper bound {upper:.10g} "
            f"for K={strike}, T={expiry}"
        )

    def objective(vol: float) -> float:
        return float(bs_price(spot, strike, expiry, rate, vol, opt, dividend_yield)) - price

    vol = 0.2 if _VOL_LO < 0.2 < _VOL_HI else 0.5 * (_VOL_LO + _VOL_HI)
    for _ in range(max_iter):
        diff = objective(vol)
        if abs(diff) < tol:
            return vol
        vega = float(bs_greeks(spot, strike, expiry, rate, vol, opt, dividend_yield).vega)
        if vega < _MIN_VEGA:
            break
        step = diff / vega
        new_vol = vol - step
        if not _VOL_LO < new_vol < _VOL_HI:
            break
        if abs(new_vol - vol) < 1e-14:  # Newton stalled below float resolution
            vol = new_vol
            break
        vol = new_vol
    if abs(objective(vol)) < tol:
        return vol

    # Price is strictly inside the bounds and BS is monotone in vol, so the
    # root is bracketed once the endpoints straddle zero.
    f_lo, f_hi = objective(_VOL_LO), objective(_VOL_HI)
    if f_lo > 0.0 or f_hi < 0.0:
        raise NumericalError(
            f"implied vol not bracketed in [{_VOL_LO}, {_VOL_HI}] for "
            f"{opt.value} K={strike}, T={expiry}, price={price}"
        )
    root = brentq(objective, _VOL_LO, _VOL_HI, xtol=1e-12, rtol=8.9e-16, maxiter=200)
    return float(root)


def strip_chain(snapshot: MarketSnapshot) -> list[IVPoint]:
    """Strip implied vols from every quote in ``snapshot``.

    Forward per expiry is ``F = S e^{(r - q)T}``. Quotes whose mid violates the
    no-arbitrage bounds are skipped; if *all* quotes fail, a
    :class:`NumericalError` is raised listing the failures.
    """
    points: list[IVPoint] = []
    failures: list[str] = []
    for quote in snapshot.quotes:
        try:
            iv = implied_vol(
                quote.mid,
                snapshot.spot,
                quote.strike,
                quote.expiry,
                snapshot.rate,
                quote.option_type,
                dividend_yield=snapshot.dividend_yield,
            )
        except NumericalError as exc:
            failures.append(str(exc))
            continue
        forward = snapshot.spot * math.exp((snapshot.rate - snapshot.dividend_yield) * quote.expiry)
        points.append(
            IVPoint(
                strike=quote.strike,
                expiry=quote.expiry,
                iv=iv,
                option_type=quote.option_type,
                forward=forward,
                log_moneyness=math.log(quote.strike / forward),
            )
        )
    if not points and failures:
        raise NumericalError(
            "all {} quotes failed IV extraction: {}".format(len(failures), "; ".join(failures))
        )
    return points


__all__ = ["IVPoint", "implied_vol", "strip_chain"]
