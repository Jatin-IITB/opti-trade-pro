"""GBM simulation, delta-hedging backtests, and the walk-forward harness."""

from optitrade.backtest.gbm import simulate_gbm_paths
from optitrade.backtest.hedging_sim import HedgeSimResult, run_delta_hedge_sim
from optitrade.backtest.market_replay import MarketReplay, StoreReplay, SyntheticVRPMarket
from optitrade.backtest.metrics import (
    annualized_sharpe,
    deflated_sharpe_ratio,
    max_drawdown,
    turnover,
)
from optitrade.backtest.walk_forward import (
    BacktestConfig,
    BacktestResult,
    FoldResult,
    WalkForwardResult,
    run_backtest,
    run_walk_forward,
)

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "FoldResult",
    "HedgeSimResult",
    "MarketReplay",
    "StoreReplay",
    "SyntheticVRPMarket",
    "WalkForwardResult",
    "annualized_sharpe",
    "deflated_sharpe_ratio",
    "max_drawdown",
    "run_backtest",
    "run_delta_hedge_sim",
    "run_walk_forward",
    "simulate_gbm_paths",
    "turnover",
]
