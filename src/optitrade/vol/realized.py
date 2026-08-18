"""OHLC realized-volatility estimators (pure numpy; annualised, decimal).

Close-to-close is the baseline sample estimator; the range-based estimators
use intraday extremes, which carry far more information per bar: under GBM
with zero drift and continuous monitoring, Parkinson (1980) is ~4.9x more
efficient than close-to-close and Garman & Klass (1980) ~7.4x. Both
range-based estimators assume zero drift and continuous monitoring —
discretely sampled bars bias the observed high/low range (and hence the
estimate) slightly low.

All estimators here are deliberately full-sample: they return one annualised
vol for the whole series. Rolling variants are just these functions applied
to slices, so callers own the windowing. The EWMA (RiskMetrics) estimator
already lives in :func:`optitrade.hedging.gamma_scalper.ewma_realized_vol`;
this module adds the OHLC range-based family and does not duplicate it.

Conventions follow ``optitrade.core.types``: vols are annualised decimals
(0.20 == 20%).

References:
- M. Parkinson (1980), "The Extreme Value Method for Estimating the Variance
  of the Rate of Return", Journal of Business 53(1), 61-65.
- M. B. Garman & M. J. Klass (1980), "On the Estimation of Security Price
  Volatilities from Historical Data", Journal of Business 53(1), 67-78.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt

# 4 ln 2 — the Parkinson (1980) range-variance normaliser.
_PARKINSON_NORM = 4.0 * math.log(2.0)
# 2 ln 2 − 1 — the Garman-Klass (1980) close-open coefficient.
_GK_CLOSE_OPEN = 2.0 * math.log(2.0) - 1.0


def _as_positive_1d(name: str, values: npt.ArrayLike) -> npt.NDArray[np.float64]:
    arr = np.asarray(values, dtype=np.float64).ravel()
    if arr.size == 0:
        raise ValueError(f"{name} must be non-empty")
    if not np.all(np.isfinite(arr)) or np.any(arr <= 0.0):
        raise ValueError(f"{name} must be finite and strictly positive")
    return arr


def close_to_close_vol(closes: npt.ArrayLike, periods_per_year: int = 252) -> float:
    """Annualised close-to-close realized vol: sample std of log returns.

    The classical estimator: per-period variance is the demeaned sample
    variance (ddof=1) of ``ln(C_t / C_{t-1})``, annualised by
    ``sqrt(periods_per_year)``. Unbiased under GBM with any constant drift
    (the mean return is subtracted), but ignores intraday information.
    """
    if periods_per_year <= 0:
        raise ValueError(f"periods_per_year must be positive, got {periods_per_year}")
    c = _as_positive_1d("closes", closes)
    if c.size < 3:
        raise ValueError(f"need >= 3 closes (>= 2 returns) for a sample std, got {c.size}")
    returns = np.diff(np.log(c))
    return float(np.std(returns, ddof=1) * math.sqrt(periods_per_year))


def parkinson_vol(highs: npt.ArrayLike, lows: npt.ArrayLike, periods_per_year: int = 252) -> float:
    """Annualised Parkinson (1980) extreme-value realized vol.

    Per-period variance is ``mean(ln(H/L)^2) / (4 ln 2)``. Assumes zero drift
    and continuously monitored highs/lows; discrete bars understate the true
    range, biasing the estimate slightly low.

    Reference: Parkinson (1980), Journal of Business 53(1), eq. (4).
    """
    if periods_per_year <= 0:
        raise ValueError(f"periods_per_year must be positive, got {periods_per_year}")
    h = _as_positive_1d("highs", highs)
    low = _as_positive_1d("lows", lows)
    if h.shape != low.shape:
        raise ValueError(f"highs {h.shape} and lows {low.shape} must align")
    if np.any(h < low):
        raise ValueError("every high must be >= its low")
    variance = float(np.mean(np.log(h / low) ** 2)) / _PARKINSON_NORM
    return float(math.sqrt(variance * periods_per_year))


def garman_klass_vol(
    opens: npt.ArrayLike,
    highs: npt.ArrayLike,
    lows: npt.ArrayLike,
    closes: npt.ArrayLike,
    periods_per_year: int = 252,
) -> float:
    """Annualised Garman & Klass (1980) OHLC realized vol.

    Per-period variance is the "practical" GK estimator (their eq. 20):

        0.5 * ln(H/L)^2 − (2 ln 2 − 1) * ln(C/O)^2

    averaged over bars. Assumes zero drift and continuous monitoring; the
    most efficient unbiased combination of range and close-open under those
    assumptions (~7.4x the close-to-close efficiency). Individual bars can
    contribute negative values; the mean is floored at zero so a degenerate
    (e.g. constant-price) series returns 0.0 rather than NaN.

    Reference: Garman & Klass (1980), Journal of Business 53(1), eq. (20).
    """
    if periods_per_year <= 0:
        raise ValueError(f"periods_per_year must be positive, got {periods_per_year}")
    o = _as_positive_1d("opens", opens)
    h = _as_positive_1d("highs", highs)
    low = _as_positive_1d("lows", lows)
    c = _as_positive_1d("closes", closes)
    if not (o.shape == h.shape == low.shape == c.shape):
        raise ValueError("opens, highs, lows and closes must have equal length")
    if np.any(h < low):
        raise ValueError("every high must be >= its low")
    per_bar = 0.5 * np.log(h / low) ** 2 - _GK_CLOSE_OPEN * np.log(c / o) ** 2
    variance = max(float(np.mean(per_bar)), 0.0)
    return float(math.sqrt(variance * periods_per_year))


__all__ = ["close_to_close_vol", "garman_klass_vol", "parkinson_vol"]
