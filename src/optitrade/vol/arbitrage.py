"""Static no-arbitrage checks on volatility surfaces.

Butterfly: by Breeden & Litzenberger (1978) the risk-neutral density is
``e^{rT} d^2C/dK^2``, so call prices off the smile must be convex in strike —
checked via divided second differences on a dense strike grid.

Calendar: total variance ``w(K, T) = iv^2 T`` must be non-decreasing in ``T``
at fixed log-moneyness (Gatheral 2006).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise
from typing import Protocol

import numpy as np

from optitrade.core import ArbitrageViolationError, OptionType
from optitrade.pricing.black_scholes import ArrayLike, bs_price

_CONVEXITY_TOL = 1e-8
_CALENDAR_TOL = 1e-10


class SurfaceLike(Protocol):
    """Structural interface both surface classes (and test stubs) satisfy."""

    @property
    def expiries(self) -> np.ndarray: ...

    def forward(self, expiry: float) -> float: ...

    def vol(self, strike: ArrayLike, expiry: ArrayLike) -> float | np.ndarray: ...


@dataclass(frozen=True, slots=True)
class Violation:
    """A single detected arbitrage violation."""

    kind: str  # "butterfly" | "calendar" | "durrleman" | "density"
    expiry: float
    strike: float
    magnitude: float
    detail: str


def check_butterfly(
    surface_like: SurfaceLike,
    expiry: float,
    spot: float,
    rate: float,
    strike_grid: np.ndarray | None = None,
) -> list[Violation]:
    """Breeden-Litzenberger convexity check at one expiry.

    Calls are priced off the smile on a dense strike grid and the divided
    second difference (slope increment) must be ``>= -tol``; a negative value
    is a butterfly-spread arbitrage (negative implied density).
    """
    if strike_grid is None:
        forward = surface_like.forward(expiry)
        atm_vol = float(np.asarray(surface_like.vol(forward, expiry)))
        # Cover +-3 ATM standard deviations, at least +-30% log-moneyness.
        width = max(3.0 * atm_vol * math.sqrt(expiry), 0.3)
        grid = forward * np.exp(np.linspace(-width, width, 121))
    else:
        grid = np.sort(np.asarray(strike_grid, dtype=float))
    vols = np.asarray(surface_like.vol(grid, expiry), dtype=float)
    calls = np.asarray(bs_price(spot, grid, expiry, rate, vols, OptionType.CALL), dtype=float)
    slopes = np.diff(calls) / np.diff(grid)
    convexity = np.diff(slopes)
    violations: list[Violation] = []
    for i in np.flatnonzero(convexity < -_CONVEXITY_TOL):
        strike = float(grid[i + 1])
        violations.append(
            Violation(
                kind="butterfly",
                expiry=expiry,
                strike=strike,
                magnitude=float(-convexity[i]),
                detail=(
                    f"call prices non-convex at K={strike:.6g}, T={expiry:.6g}: "
                    f"slope increment {float(convexity[i]):.3e}"
                ),
            )
        )
    return violations


def check_calendar(
    surface_like: SurfaceLike,
    moneyness_grid: np.ndarray | None = None,
) -> list[Violation]:
    """Total variance must be non-decreasing in expiry at fixed log-moneyness."""
    lm = (
        np.linspace(-0.3, 0.3, 13)
        if moneyness_grid is None
        else np.asarray(moneyness_grid, dtype=float)
    )
    expiries = np.asarray(surface_like.expiries, dtype=float)
    violations: list[Violation] = []
    for t_near, t_far in pairwise(expiries):
        k_near = surface_like.forward(float(t_near)) * np.exp(lm)
        k_far = surface_like.forward(float(t_far)) * np.exp(lm)
        v_near = np.asarray(surface_like.vol(k_near, float(t_near)), dtype=float)
        v_far = np.asarray(surface_like.vol(k_far, float(t_far)), dtype=float)
        w_near = v_near * v_near * t_near
        w_far = v_far * v_far * t_far
        for j in np.flatnonzero(w_far < w_near - _CALENDAR_TOL):
            violations.append(
                Violation(
                    kind="calendar",
                    expiry=float(t_far),
                    strike=float(k_far[j]),
                    magnitude=float(w_near[j] - w_far[j]),
                    detail=(
                        f"total variance falls from {float(w_near[j]):.6g} (T={t_near:.6g}) "
                        f"to {float(w_far[j]):.6g} (T={t_far:.6g}) at lm={float(lm[j]):.4g}"
                    ),
                )
            )
    return violations


def validate_surface(
    surface: SurfaceLike,
    spot: float,
    rate: float,
    raise_on_violation: bool = False,
) -> list[Violation]:
    """Run butterfly checks at every quoted expiry plus the calendar check.

    Returns all violations; if ``raise_on_violation`` is set and any exist,
    raises :class:`ArbitrageViolationError` listing them.
    """
    violations: list[Violation] = []
    for expiry in np.asarray(surface.expiries, dtype=float):
        violations.extend(check_butterfly(surface, float(expiry), spot, rate))
    violations.extend(check_calendar(surface))
    if raise_on_violation and violations:
        lines = "; ".join(v.detail for v in violations)
        raise ArbitrageViolationError(
            f"surface fails static no-arbitrage with {len(violations)} violation(s): {lines}"
        )
    return violations


def check_durrleman(
    surface_like: SurfaceLike,
    expiry: float,
    forward: float,
    k_grid: np.ndarray | None = None,
    tol: float = 1e-8,
) -> list[Violation]:
    """Durrleman's condition on one total-variance slice.

    A smile ``w(k)`` (total variance at log-moneyness ``k = ln(K/F)``) is free
    of butterfly arbitrage iff (Durrleman 2004; Gatheral & Jacquier 2014,
    eq. (2.1))

        g(k) = (1 - k w'/(2w))^2 - (w'^2/4)(1/w + 1/4) + w''/2 >= 0

    everywhere, ``g`` being proportional to the risk-neutral density.
    Derivatives are taken by central finite differences (``np.gradient``) on a
    dense ``k`` grid; the two boundary points use one-sided differences and are
    excluded from flagging. Violations carry ``kind="durrleman"``.
    """
    k = np.linspace(-1.5, 1.5, 301) if k_grid is None else np.sort(np.asarray(k_grid, dtype=float))
    strikes = forward * np.exp(k)
    vols = np.asarray(surface_like.vol(strikes, expiry), dtype=float)
    w = np.maximum(vols * vols * expiry, 1e-16)
    w_p = np.gradient(w, k)
    w_pp = np.gradient(w_p, k)
    g = (1.0 - k * w_p / (2.0 * w)) ** 2 - 0.25 * w_p * w_p * (1.0 / w + 0.25) + 0.5 * w_pp
    violations: list[Violation] = []
    interior = np.arange(1, k.size - 1)
    for i in interior[g[interior] < -tol]:
        strike = float(strikes[i])
        violations.append(
            Violation(
                kind="durrleman",
                expiry=expiry,
                strike=strike,
                magnitude=float(-g[i]),
                detail=(
                    f"Durrleman g={float(g[i]):.3e} < 0 at k={float(k[i]):.4g} "
                    f"(K={strike:.6g}), T={expiry:.6g}"
                ),
            )
        )
    return violations


__all__ = [
    "SurfaceLike",
    "Violation",
    "check_butterfly",
    "check_calendar",
    "check_durrleman",
    "validate_surface",
]
