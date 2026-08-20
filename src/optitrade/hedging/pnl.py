"""Taylor-expansion P&L attribution and theta-tracking diagnostics.

Attribution splits a realized P&L into the classic Greek buckets

    dPnL ~= delta * dS + 0.5 * gamma * dS**2 + theta * dt + vega * dsigma

with everything unexplained (higher-order terms, cross terms, discreteness)
reported as the residual. Units follow ``optitrade.core.types``: theta per
year, vega per unit vol.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from optitrade.core import Greeks, NumericalError

# Below this absolute total-theta the tracking ratio is numerically meaningless.
_THETA_DENOMINATOR_FLOOR = 1e-12


@dataclass(frozen=True, slots=True)
class PnLAttribution:
    """Greek-bucketed P&L decomposition; ``total = explained + residual``."""

    delta_pnl: float
    gamma_pnl: float
    theta_pnl: float
    vega_pnl: float
    residual: float
    total: float


def attribute_pnl(
    greeks: Greeks, d_spot: float, d_vol: float, d_time: float, total_pnl: float
) -> PnLAttribution:
    """Second-order Taylor attribution of ``total_pnl`` over one interval.

    Args:
        greeks: Position Greeks at the start of the interval (already scaled
            by signed quantity).
        d_spot: Spot change over the interval.
        d_vol: Implied-vol change (decimal, e.g. 0.01 = 1 vol point).
        d_time: Elapsed calendar time as a year fraction.
        total_pnl: The realized mark-to-market P&L being explained.
    """
    delta_pnl = greeks.delta * d_spot
    gamma_pnl = 0.5 * greeks.gamma * d_spot * d_spot
    theta_pnl = greeks.theta * d_time
    vega_pnl = greeks.vega * d_vol
    explained = delta_pnl + gamma_pnl + theta_pnl + vega_pnl
    return PnLAttribution(
        delta_pnl=delta_pnl,
        gamma_pnl=gamma_pnl,
        theta_pnl=theta_pnl,
        vega_pnl=vega_pnl,
        residual=total_pnl - explained,
        total=total_pnl,
    )


def theta_tracking_error(hedged_pnl: np.ndarray, theta_pnl: np.ndarray) -> float:
    """Relative gap between aggregate hedged P&L and aggregate theta P&L.

    Defined as ``|sum(hedged_pnl) - sum(theta_pnl)| / |sum(theta_pnl)|`` --
    the report metric "hedging P&L within ~x% of theoretical theta". For a
    delta-hedged book at realized == implied vol the gamma harvest offsets
    theta decay, so feeding the spot-move P&L against the theta-decay
    magnitude yields a small fraction.

    Raises:
        NumericalError: If ``|sum(theta_pnl)|`` is ~0 (ratio undefined).
    """
    hedged_total = float(np.sum(np.asarray(hedged_pnl, dtype=float)))
    theta_total = float(np.sum(np.asarray(theta_pnl, dtype=float)))
    if abs(theta_total) < _THETA_DENOMINATOR_FLOOR:
        raise NumericalError(
            f"total theta P&L {theta_total} is ~0; theta tracking error is undefined"
        )
    return abs(hedged_total - theta_total) / abs(theta_total)


__all__ = ["PnLAttribution", "attribute_pnl", "theta_tracking_error"]
