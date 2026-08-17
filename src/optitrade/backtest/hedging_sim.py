"""Monte-Carlo delta-hedging backtest on GBM paths.

Holds a fixed option position marked at a constant implied vol while the
underlying realizes a (possibly different) vol, and delta-hedges it with a
:class:`~optitrade.hedging.delta_hedger.DeltaHedger` band policy. This is the
harness behind the classic results: at realized == implied the hedged book's
gamma harvest offsets theta decay (P&L ~= 0), at realized > implied a long
option earns ~0.5 * Gamma * S**2 * (sigma_R**2 - sigma_I**2) per unit time,
and proportional costs eat into that edge.

Model notes (kept deliberately simple, documented rather than hidden):
- Paths are simulated with drift equal to the risk-free rate (risk-neutral);
  a delta-hedged book is insensitive to drift to first order.
- The hedge cash account accrues interest at ``rate``; the option premium is
  treated as sunk (P&L is mark-to-market changes, not financed).
- Proportional cost ``hedger.band_params.proportional_cost`` is charged on
  traded value; the terminal hedge is marked, not liquidated (no final cost).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from optitrade.backtest.gbm import simulate_gbm_paths
from optitrade.core import OptionContract
from optitrade.hedging.delta_hedger import DeltaHedger
from optitrade.hedging.pnl import theta_tracking_error
from optitrade.pricing import bs_greeks, bs_price


@dataclass(frozen=True)
class HedgeSimResult:
    """Aggregate outcome of a delta-hedging Monte-Carlo run.

    Attributes:
        final_pnl: Per-path final P&L (option MTM + hedge P&L + interest,
            net of transaction costs), shape ``(n_paths,)``.
        mean_pnl: Mean of ``final_pnl`` across paths.
        std_pnl: Sample standard deviation (ddof=1) of ``final_pnl``.
        mean_costs: Mean cumulative transaction costs per path.
        n_rebalances_mean: Mean number of rebalancing trades per path.
        theta_tracking: ``|sum spot-move P&L - sum theta decay| / |sum theta|``
            aggregated over paths (fraction; small when the gamma harvest
            tracks theoretical theta, e.g. at realized == implied).
        decision_counts: Per-path journal-ready counts, one
            ``{"hold": h, "rebalance": r}`` dict per path.
    """

    final_pnl: npt.NDArray[np.float64]
    mean_pnl: float
    std_pnl: float
    mean_costs: float
    n_rebalances_mean: float
    theta_tracking: float
    decision_counts: tuple[dict[str, int], ...]


def run_delta_hedge_sim(
    option: OptionContract,
    implied_vol: float,
    realized_vol: float,
    spot: float,
    rate: float,
    hedger: DeltaHedger,
    dt: float,
    n_steps: int,
    n_paths: int,
    seed: int,
    quantity: float = 1.0,
) -> HedgeSimResult:
    """Delta-hedge ``quantity`` options along seeded GBM paths.

    Each step the position is re-Greeked at ``implied_vol`` (vectorised across
    paths), the hedger's Whalley-Wilmott band is applied per path, and any
    hedge trades in the underlying pay the hedger's proportional cost. The
    option is marked with :func:`bs_price` at ``implied_vol`` along the path.

    ``theta_tracking`` compares the spot-move P&L (total hedged P&L excluding
    costs, minus the theta accrual) against the theta-decay magnitude via
    :func:`theta_tracking_error`; at realized == implied the two offset, so
    the fraction measures residual hedging error relative to theta.
    """
    if implied_vol <= 0.0:
        raise ValueError(f"implied_vol must be positive, got {implied_vol}")
    if realized_vol < 0.0:
        raise ValueError(f"realized_vol must be >= 0, got {realized_vol}")
    if n_steps * dt > option.expiry + 1e-9:
        raise ValueError(
            f"horizon n_steps * dt = {n_steps * dt} exceeds option expiry {option.expiry}"
        )

    paths = simulate_gbm_paths(spot, rate, realized_vol, dt, n_steps, n_paths, seed)
    taus = option.expiry - dt * np.arange(n_steps + 1, dtype=np.float64)
    units = quantity * option.lot_size

    values = np.asarray(
        bs_price(paths, option.strike, taus, rate, implied_vol, option.option_type),
        dtype=np.float64,
    )
    # Greeks on the decision grid (all steps except the terminal one).
    greeks = bs_greeks(
        paths[:, :-1], option.strike, taus[:-1], rate, implied_vol, option.option_type
    )
    option_deltas = units * greeks.delta
    option_gammas = units * greeks.gamma
    theta_pnl = np.sum(units * greeks.theta * dt, axis=1)

    cost_rate = hedger.band_params.proportional_cost
    growth = math.exp(rate * dt) - 1.0
    shares = np.zeros(n_paths, dtype=np.float64)
    cash = np.zeros(n_paths, dtype=np.float64)
    costs = np.zeros(n_paths, dtype=np.float64)
    n_rebalances = np.zeros(n_paths, dtype=np.int64)
    n_holds = np.zeros(n_paths, dtype=np.int64)

    for step in range(n_steps):
        spots_now = paths[:, step]
        for p in range(n_paths):
            decision = hedger.decide(
                portfolio_delta=float(option_deltas[p, step] + shares[p]),
                gamma=float(option_gammas[p, step]),
                spot=float(spots_now[p]),
                realized_vol=realized_vol,
                implied_vol=implied_vol,
            )
            if decision.action == "rebalance" and decision.order is not None:
                traded = decision.order.quantity
                trade_cost = cost_rate * abs(traded) * spots_now[p]
                shares[p] += traded
                cash[p] -= traded * spots_now[p] + trade_cost
                costs[p] += trade_cost
                n_rebalances[p] += 1
            else:
                n_holds[p] += 1
        cash += cash * growth

    final_pnl = units * (values[:, -1] - values[:, 0]) + shares * paths[:, -1] + cash
    spot_move_pnl = (final_pnl + costs) - theta_pnl
    theta_tracking = theta_tracking_error(spot_move_pnl, -theta_pnl)

    return HedgeSimResult(
        final_pnl=final_pnl,
        mean_pnl=float(np.mean(final_pnl)),
        std_pnl=float(np.std(final_pnl, ddof=1)) if n_paths > 1 else 0.0,
        mean_costs=float(np.mean(costs)),
        n_rebalances_mean=float(np.mean(n_rebalances)),
        theta_tracking=theta_tracking,
        decision_counts=tuple(
            {"hold": int(h), "rebalance": int(r)}
            for h, r in zip(n_holds, n_rebalances, strict=True)
        ),
    )


__all__ = ["HedgeSimResult", "run_delta_hedge_sim"]
