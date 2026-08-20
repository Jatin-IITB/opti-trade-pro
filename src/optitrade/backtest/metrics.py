"""Backtest performance metrics, including the deflated Sharpe ratio.

The deflated Sharpe ratio (DSR) answers the question a raw Sharpe cannot:
"given how many configurations were tried, what is the probability this
Sharpe is real rather than the maximum of noise?" It deflates the observed
Sharpe by the expected maximum Sharpe of ``n_trials`` unskilled trials and
adjusts the estimator variance for non-normal P&L.

Reference: D. H. Bailey & M. López de Prado (2014), "The Deflated Sharpe
Ratio: Correcting for Selection Bias, Backtest Overfitting, and
Non-Normality", Journal of Portfolio Management 40(5), 94-107.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt
from scipy.stats import norm


def annualized_sharpe(daily_pnl: npt.ArrayLike, periods_per_year: int = 252) -> float:
    """Annualised Sharpe ratio of a per-period P&L series.

    ``mean / std`` (ddof=1) of the P&L, scaled by ``sqrt(periods_per_year)``.
    P&L is in currency, not returns — fine for ranking and significance on a
    fixed capital base. Fewer than two observations or (near-)zero dispersion
    returns 0.0: "no variance" is treated as "no evidence of skill", never a
    division by zero.
    """
    if periods_per_year <= 0:
        raise ValueError(f"periods_per_year must be positive, got {periods_per_year}")
    pnl = np.asarray(daily_pnl, dtype=np.float64).ravel()
    if pnl.size < 2:
        return 0.0
    std = float(np.std(pnl, ddof=1))
    if std <= 0.0 or not math.isfinite(std):
        return 0.0
    return float(np.mean(pnl)) / std * math.sqrt(periods_per_year)


def max_drawdown(equity: npt.ArrayLike) -> float:
    """Maximum peak-to-trough drawdown of an equity curve, as a fraction.

    ``max((peak - equity) / peak)`` over the running peak; 0.0 for a
    monotonically rising curve. Assumes a positive equity curve; any point
    where the running peak is non-positive contributes 0 (a book that never
    held positive equity has no meaningful fractional drawdown).
    """
    curve = np.asarray(equity, dtype=np.float64).ravel()
    if curve.size == 0:
        raise ValueError("equity curve must be non-empty")
    peaks = np.maximum.accumulate(curve)
    with np.errstate(divide="ignore", invalid="ignore"):
        drawdowns = np.where(peaks > 0.0, (peaks - curve) / peaks, 0.0)
    return float(np.max(drawdowns, initial=0.0))


def turnover(fills_notional: npt.ArrayLike, equity_mean: float) -> float:
    """Gross traded notional as a multiple of average equity.

    ``sum(|fills_notional|) / equity_mean`` — the standard "how many times
    did the book turn over" measure for cost sensitivity.
    """
    if equity_mean <= 0.0:
        raise ValueError(f"equity_mean must be positive, got {equity_mean}")
    fills = np.asarray(fills_notional, dtype=np.float64).ravel()
    return float(np.sum(np.abs(fills))) / equity_mean


def deflated_sharpe_ratio(
    observed_sr: float,
    n_trials: int,
    n_obs: int,
    skew: float,
    kurtosis: float,
) -> float:
    """Deflated Sharpe ratio per Bailey & López de Prado (2014).

    UNITS — read this before calling:
    - ``observed_sr`` is the PER-PERIOD Sharpe (e.g. daily mean/std), NOT the
      annualised Sharpe. Passing an annualised value inflates the result.
    - ``kurtosis`` is the RAW (non-excess) kurtosis: 3.0 for a Gaussian. When
      using :func:`scipy.stats.kurtosis`, pass ``fisher=False``.

    The statistic is

        DSR = Phi( (SR - SR0) * sqrt(n_obs - 1)
                   / sqrt(1 - skew*SR + ((kurtosis - 1)/4) * SR^2) )

    where ``SR0`` is the expected maximum Sharpe of ``n_trials`` unskilled
    (iid standard-normal) trials:

        SR0 = sqrt(1/(n_obs-1)) * ((1-gamma) * Phi^{-1}(1 - 1/n_trials)
                                   + gamma * Phi^{-1}(1 - 1/(n_trials*e)))

    with ``gamma`` the Euler-Mascheroni constant. That approximation is
    asymptotic in ``n_trials`` and degenerates at ``n_trials = 1``
    (``Phi^{-1}(0) = -inf``), so for a single trial the exact expected
    maximum of one standard-normal draw — zero — is used instead: the result
    is then the probabilistic Sharpe ratio against a zero benchmark, which
    still discounts the raw ``Phi(SR * sqrt(n_obs))`` probability via the
    ``n_obs - 1`` and the skew/kurtosis variance adjustment.

    Returns a probability in [0, 1]. Raises when the variance adjustment is
    non-positive (pathological skew/kurtosis for the given SR) rather than
    returning NaN — fail loud, not wrong.
    """
    if n_trials < 1:
        raise ValueError(f"n_trials must be >= 1, got {n_trials}")
    if n_obs < 2:
        raise ValueError(f"n_obs must be >= 2, got {n_obs}")
    gamma = float(np.euler_gamma)
    if n_trials == 1:
        sr0 = 0.0
    else:
        sr0 = math.sqrt(1.0 / (n_obs - 1)) * (
            (1.0 - gamma) * float(norm.ppf(1.0 - 1.0 / n_trials))
            + gamma * float(norm.ppf(1.0 - 1.0 / (n_trials * math.e)))
        )
    variance_adj = 1.0 - skew * observed_sr + ((kurtosis - 1.0) / 4.0) * observed_sr**2
    if variance_adj <= 0.0:
        raise ValueError(
            f"non-positive variance adjustment {variance_adj:.6g} for SR={observed_sr}, "
            f"skew={skew}, kurtosis={kurtosis}; DSR is undefined here"
        )
    statistic = (observed_sr - sr0) * math.sqrt(n_obs - 1.0) / math.sqrt(variance_adj)
    return float(norm.cdf(statistic))


__all__ = ["annualized_sharpe", "deflated_sharpe_ratio", "max_drawdown", "turnover"]
