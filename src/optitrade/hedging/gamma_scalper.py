"""Gamma-scalping overlay: realized-vol estimation and band scaling.

A long-gamma, delta-hedged book earns approximately

    dPnL ~= 0.5 * Gamma * S**2 * (sigma_R**2 - sigma_I**2) * dt

per unit time (realized variance versus the implied variance paid via theta).
When realized vol runs above implied, each rebalance locks in gamma gains, so
the hedging band should tighten (rebalance more, harvest gamma). When realized
runs below implied there is little to harvest and every trade is a cost, so
the band should widen. This module estimates realized vol (RiskMetrics EWMA)
and maps the RV/IV ratio to a multiplicative band scale.

References: J.P. Morgan/Reuters, "RiskMetrics -- Technical Document" (4th ed.,
1996) for the EWMA estimator; Whalley & Wilmott (1997) for the base band.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from optitrade.hedging.band import BandParams


def ewma_realized_vol(returns: np.ndarray, lam: float = 0.94, periods_per_year: int = 252) -> float:
    """Annualised realized volatility via the RiskMetrics (1996) EWMA.

    The per-period variance estimate is the exponentially weighted mean of
    squared returns (zero-mean assumption, per RiskMetrics):

        sigma**2 = (1 - lam) / (1 - lam**n) * sum_k lam**k * r_{t-k}**2

    with the most recent return weighted ``(1 - lam)``. The finite-sample
    normalisation ``1 - lam**n`` makes the weights sum to one exactly. The
    result is annualised by ``sqrt(periods_per_year)``.

    Args:
        returns: 1-D array of per-period (e.g. daily) log returns.
        lam: Decay factor in (0, 1); RiskMetrics recommends 0.94 for daily.
        periods_per_year: Return periods per year (252 for daily).
    """
    if not 0.0 < lam < 1.0:
        raise ValueError(f"lam must be in (0, 1), got {lam}")
    if periods_per_year <= 0:
        raise ValueError(f"periods_per_year must be positive, got {periods_per_year}")
    r = np.asarray(returns, dtype=float).ravel()
    n = r.size
    if n == 0:
        raise ValueError("returns must be non-empty")
    # Powers run oldest -> newest so r[-1] gets weight (1 - lam) * lam**0.
    powers = np.arange(n - 1, -1, -1, dtype=float)
    weights = (1.0 - lam) * lam**powers / (1.0 - lam**n)
    variance = float(np.sum(weights * r * r))
    return float(np.sqrt(variance * periods_per_year))


@dataclass(frozen=True, slots=True)
class ScalpingParams:
    """Band-scaling rule parameters keyed off the RV/IV ratio.

    Attributes:
        band_params: Base Whalley-Wilmott band parameters being scaled.
        rv_iv_low: RV/IV ratio at or below which the band is fully widened.
        rv_iv_high: RV/IV ratio at or above which the band is fully tightened.
        tighten_factor: Band multiplier in the high-RV regime (< 1 tightens).
        widen_factor: Band multiplier in the low-RV regime (> 1 widens).
    """

    band_params: BandParams
    rv_iv_low: float = 0.8
    rv_iv_high: float = 1.2
    tighten_factor: float = 0.5
    widen_factor: float = 2.0

    def __post_init__(self) -> None:
        if not 0.0 < self.rv_iv_low < self.rv_iv_high:
            raise ValueError(
                f"require 0 < rv_iv_low < rv_iv_high, got {self.rv_iv_low}, {self.rv_iv_high}"
            )
        if self.tighten_factor <= 0.0 or self.widen_factor <= 0.0:
            raise ValueError("tighten_factor and widen_factor must be positive")


def scalping_band_scale(realized_vol: float, implied_vol: float, params: ScalpingParams) -> float:
    """Multiplicative scale for the no-transaction band from the RV/IV ratio.

    Economics: a delta-hedged long-gamma position earns approximately
    ``0.5 * Gamma * S**2 * (sigma_R**2 - sigma_I**2) * dt`` -- realized
    variance collected through rebalancing minus implied variance paid through
    theta. When ``RV/IV >= rv_iv_high`` gamma is worth harvesting, so return
    ``tighten_factor`` (narrower band, rebalance more). When
    ``RV/IV <= rv_iv_low`` trades mostly burn costs, so return
    ``widen_factor`` (wider band, save costs). Between the thresholds the
    scale interpolates linearly, so it is continuous at both edges.

    Pure function of its arguments; the caller multiplies the
    Whalley-Wilmott half-width by the returned scale.
    """
    if implied_vol <= 0.0:
        raise ValueError(f"implied_vol must be positive, got {implied_vol}")
    if realized_vol < 0.0:
        raise ValueError(f"realized_vol must be >= 0, got {realized_vol}")
    ratio = realized_vol / implied_vol
    if ratio >= params.rv_iv_high:
        return params.tighten_factor
    if ratio <= params.rv_iv_low:
        return params.widen_factor
    t = (ratio - params.rv_iv_low) / (params.rv_iv_high - params.rv_iv_low)
    return params.widen_factor + t * (params.tighten_factor - params.widen_factor)


__all__ = ["ScalpingParams", "ewma_realized_vol", "scalping_band_scale"]
