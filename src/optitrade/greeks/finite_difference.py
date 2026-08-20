"""Model-agnostic finite-difference Greeks (bump-and-reprice).

Estimates the full :class:`~optitrade.core.types.Greeks` set from *any* pricing
callable:

- delta, vega, rho — central differences, truncation error ``O(h^2)``;
- gamma, volga — second central differences, ``O(h^2)``;
- vanna — four-point cross central difference, ``O(h^2)``;
- theta — forward difference toward expiry, ``O(h)``:
  ``theta = (P(tau - dt) - P(tau)) / dt``, the *calendar-time* decay
  ``-dP/dtau``, matching the sign of the analytic Black-Scholes theta in
  :mod:`optitrade.pricing.black_scholes`.

Stencils: Abramowitz & Stegun, *Handbook of Mathematical Functions*, sec. 25.3;
Greeks by finite differences: Glasserman, *Monte Carlo Methods in Financial
Engineering* (Springer, 2004), sec. 7.1.

The engine never inspects the model. Anything with the :data:`PriceFn`
signature works: Black-Scholes closures, SABR-implied-vol repricers, PDE or
fixed-seed Monte-Carlo pricers.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from optitrade.core import Greeks, NumericalError

PriceFn = Callable[[float, float, float, float], float]
"""Pricing callable with argument order ``(spot, vol, rate, expiry)``.

``expiry`` is a year fraction of time *to* expiry; ``vol`` and ``rate`` are
annualised decimals — the conventions of :mod:`optitrade.core.types`.
"""


@dataclass(frozen=True, slots=True)
class FDBumps:
    """Bump sizes for :func:`fd_greeks`.

    ``rel_spot`` is relative (spot bump = ``spot * rel_spot``); the others are
    absolute. ``abs_time`` defaults to one calendar day (1/365) — the
    practitioner's "1-day theta". Shrink it (e.g. to ``1e-6``) to approximate
    instantaneous theta; the forward stencil's error is ``O(abs_time)``.
    """

    rel_spot: float = 1e-4
    abs_vol: float = 1e-4
    abs_rate: float = 1e-4
    abs_time: float = 1.0 / 365.0


def fd_greeks(
    price_fn: PriceFn,
    spot: float,
    vol: float,
    rate: float,
    expiry: float,
    bumps: FDBumps = FDBumps(),  # noqa: B008
) -> Greeks:
    """Estimate all Greeks of ``price_fn`` at ``(spot, vol, rate, expiry)``.

    Uses 12 price evaluations: base, spot up/down, vol up/down, rate up/down,
    the four spot-vol cross corners, and one time-decayed reprice. The default
    ``bumps`` argument is safe to share: :class:`FDBumps` is frozen.

    Raises:
        NumericalError: if ``spot``/``expiry`` are not positive or any
            resulting Greek is non-finite (NaN/inf from ``price_fn``).
    """
    if not (spot > 0.0 and expiry > 0.0):
        raise NumericalError(f"fd_greeks requires spot > 0 and expiry > 0, got {spot=}, {expiry=}")

    ds = spot * bumps.rel_spot
    dv = bumps.abs_vol
    dr = bumps.abs_rate
    # The theta bump must not cross expiry: a 1-day bump on a shorter-dated
    # option would reprice at negative time-to-expiry. Halving tau keeps the
    # decayed expiry strictly positive.
    dt = bumps.abs_time if bumps.abs_time < expiry else 0.5 * expiry

    p_0 = price_fn(spot, vol, rate, expiry)
    p_su = price_fn(spot + ds, vol, rate, expiry)
    p_sd = price_fn(spot - ds, vol, rate, expiry)
    p_vu = price_fn(spot, vol + dv, rate, expiry)
    p_vd = price_fn(spot, vol - dv, rate, expiry)
    p_ru = price_fn(spot, vol, rate + dr, expiry)
    p_rd = price_fn(spot, vol, rate - dr, expiry)
    p_uu = price_fn(spot + ds, vol + dv, rate, expiry)
    p_ud = price_fn(spot + ds, vol - dv, rate, expiry)
    p_du = price_fn(spot - ds, vol + dv, rate, expiry)
    p_dd = price_fn(spot - ds, vol - dv, rate, expiry)
    p_t = price_fn(spot, vol, rate, expiry - dt)

    greeks = Greeks(
        delta=(p_su - p_sd) / (2.0 * ds),
        gamma=(p_su - 2.0 * p_0 + p_sd) / (ds * ds),
        vega=(p_vu - p_vd) / (2.0 * dv),
        theta=(p_t - p_0) / dt,
        rho=(p_ru - p_rd) / (2.0 * dr),
        vanna=(p_uu - p_ud - p_du + p_dd) / (4.0 * ds * dv),
        volga=(p_vu - 2.0 * p_0 + p_vd) / (dv * dv),
    )
    finite = (
        math.isfinite(greeks.delta)
        and math.isfinite(greeks.gamma)
        and math.isfinite(greeks.vega)
        and math.isfinite(greeks.theta)
        and math.isfinite(greeks.rho)
        and math.isfinite(greeks.vanna)
        and math.isfinite(greeks.volga)
    )
    if not finite:
        raise NumericalError("fd_greeks produced a non-finite Greek; check price_fn and bumps")
    return greeks


__all__ = ["FDBumps", "PriceFn", "fd_greeks"]
