"""Breeden-Litzenberger risk-neutral density as a surface validation gate.

Breeden & Litzenberger (1978): the risk-neutral density of the terminal spot
is the discounted second strike derivative of the call price,

    pdf(K) = e^{rT} d^2C/dK^2.

A well-formed surface therefore implies a pdf that is non-negative,
integrates to one, and has mean equal to the forward ``F = S e^{(r-q)T}``
(martingale condition). :func:`extract_rnd` prices calls off any
``SurfaceLike`` on a dense uniform strike grid and differentiates by central
differences; :func:`validate_rnd` turns diagnostic breaches into
:class:`~optitrade.vol.arbitrage.Violation` records (``kind="density"``), and
:func:`rnd_gate` runs the pair across expiries, optionally raising
:class:`~optitrade.core.ArbitrageViolationError` — the same fail-closed shape
as ``validate_surface``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from optitrade.core import ArbitrageViolationError, OptionType
from optitrade.pricing.black_scholes import bs_price
from optitrade.vol.arbitrage import SurfaceLike, Violation

_MIN_GRID = 5
_MIN_INTEGRAL = 1e-12


@dataclass(frozen=True, slots=True)
class RNDResult:
    """Risk-neutral density on a strike grid, with integral diagnostics.

    ``strikes``/``pdf`` are the interior grid points (central differences
    consume one point at each end). ``implied_mean`` is normalised by the
    integral so truncation of far wings does not masquerade as drift; it is
    NaN when the integral is not meaningfully positive.
    """

    expiry: float
    strikes: np.ndarray
    pdf: np.ndarray
    min_pdf: float
    integral: float
    implied_mean: float


def extract_rnd(
    surface_like: SurfaceLike,
    expiry: float,
    spot: float,
    rate: float,
    dividend_yield: float = 0.0,
    n_grid: int = 400,
) -> RNDResult:
    """Extract the Breeden-Litzenberger (1978) density from a surface slice.

    Calls are priced off the smile on a uniform strike grid spanning roughly
    +-5 ATM standard deviations (at least +-50% log-moneyness) around the
    forward, then ``pdf(K) = e^{rT} d^2C/dK^2`` by central second differences.
    """
    if expiry <= 0.0:
        raise ValueError(f"expiry must be positive, got {expiry}")
    if n_grid < _MIN_GRID:
        raise ValueError(f"n_grid must be >= {_MIN_GRID}, got {n_grid}")
    forward = spot * math.exp((rate - dividend_yield) * expiry)
    atm_vol = float(np.asarray(surface_like.vol(forward, expiry), dtype=float))
    width = max(5.0 * atm_vol * math.sqrt(expiry), 0.5)
    strikes = np.linspace(forward * math.exp(-width), forward * math.exp(width), n_grid)
    vols = np.asarray(surface_like.vol(strikes, expiry), dtype=float)
    calls = np.asarray(
        bs_price(spot, strikes, expiry, rate, vols, OptionType.CALL, dividend_yield),
        dtype=float,
    )
    h = float(strikes[1] - strikes[0])
    curvature = (calls[2:] - 2.0 * calls[1:-1] + calls[:-2]) / (h * h)
    pdf = math.exp(rate * expiry) * curvature
    inner = strikes[1:-1]
    integral = float(np.trapezoid(pdf, inner))
    implied_mean = (
        float(np.trapezoid(inner * pdf, inner) / integral)
        if integral > _MIN_INTEGRAL
        else float("nan")
    )
    return RNDResult(
        expiry=expiry,
        strikes=inner,
        pdf=pdf,
        min_pdf=float(pdf.min()),
        integral=integral,
        implied_mean=implied_mean,
    )


def validate_rnd(
    result: RNDResult,
    forward: float,
    tol_negative: float = 1e-4,
    tol_integral: float = 0.02,
    tol_mean_rel: float = 0.01,
) -> list[Violation]:
    """Flag density defects: negative mass, bad integral, mean off the forward.

    Every violation carries ``kind="density"``. Aggregate checks (integral,
    mean) use ``strike=nan`` since they are not localised to one strike.
    """
    violations: list[Violation] = []
    for i in np.flatnonzero(result.pdf < -tol_negative):
        strike = float(result.strikes[i])
        violations.append(
            Violation(
                kind="density",
                expiry=result.expiry,
                strike=strike,
                magnitude=float(-result.pdf[i]),
                detail=(
                    f"negative risk-neutral density {float(result.pdf[i]):.3e} "
                    f"at K={strike:.6g}, T={result.expiry:.6g}"
                ),
            )
        )
    gap = abs(result.integral - 1.0)
    if gap > tol_integral:
        violations.append(
            Violation(
                kind="density",
                expiry=result.expiry,
                strike=float("nan"),
                magnitude=gap,
                detail=(
                    f"density integrates to {result.integral:.6g} (|error| {gap:.3e} "
                    f"> {tol_integral}) at T={result.expiry:.6g}"
                ),
            )
        )
    mean_err = (
        float("inf")
        if math.isnan(result.implied_mean)
        else abs(result.implied_mean - forward) / forward
    )
    if mean_err > tol_mean_rel:
        violations.append(
            Violation(
                kind="density",
                expiry=result.expiry,
                strike=float("nan"),
                magnitude=mean_err,
                detail=(
                    f"density mean {result.implied_mean:.6g} vs forward {forward:.6g} "
                    f"(rel error {mean_err:.3e} > {tol_mean_rel}) at T={result.expiry:.6g}"
                ),
            )
        )
    return violations


def rnd_gate(
    surface: SurfaceLike,
    expiries: Iterable[float],
    spot: float,
    rate: float,
    dividend_yield: float = 0.0,
    n_grid: int = 400,
    raise_on_violation: bool = False,
) -> list[Violation]:
    """Run extract + validate per expiry; optionally raise on any violation.

    Convenience gate matching ``validate_surface``'s contract: returns every
    violation found, and raises :class:`ArbitrageViolationError` listing them
    when ``raise_on_violation`` is set.
    """
    violations: list[Violation] = []
    for t in expiries:
        expiry = float(t)
        result = extract_rnd(surface, expiry, spot, rate, dividend_yield, n_grid)
        forward = spot * math.exp((rate - dividend_yield) * expiry)
        violations.extend(validate_rnd(result, forward))
    if raise_on_violation and violations:
        lines = "; ".join(v.detail for v in violations)
        raise ArbitrageViolationError(
            f"risk-neutral density gate failed with {len(violations)} violation(s): {lines}"
        )
    return violations


__all__ = ["RNDResult", "extract_rnd", "rnd_gate", "validate_rnd"]
