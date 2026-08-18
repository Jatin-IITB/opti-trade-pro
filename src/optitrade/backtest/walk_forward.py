"""Walk-forward backtesting harness with fail-closed risk review.

``run_backtest`` drives one strategy over a day sequence: mark to market off
the day's surface, ask the strategy, route every option order through the
:class:`~optitrade.risk.RiskEngine` (rejections skip the fill — ADR-008),
delta-hedge daily via the Whalley-Wilmott band, and account real costs.
``run_walk_forward`` wraps it in rolling train/test folds, picks each fold's
config on train Sharpe only, stitches out-of-sample P&L, and reports the
deflated Sharpe ratio so grid size is charged against significance (Bailey &
López de Prado 2014).

Model notes (deliberate simplifications, documented rather than hidden):
- Marking: positions mark at ``bs_price`` under the day's surface vol
  (fallback: the ``atm_iv`` feature, then the day's realized vol). Expired
  positions roll off at intrinsic value against that day's spot, cost-free.
- Fills: option fills pay half of ``spread_frac`` against themselves on top
  of the order price, plus the itemised cost model. Hedge fills in the
  underlying trade at spot and pay ``hedge_cost_frac`` of traded value
  (spread and fees bundled into one proportional rate).
- Hedges bypass the pre-trade risk battery: they are risk-*reducing* by
  construction (the hedger targets flat delta) and are journaled instead.
- Margin: ``margin_available`` is account equity and ``margin_required`` an
  order's premium notional — a placeholder until a SPAN-style model exists.
- Cash earns no interest; day count is ACT/365 off ``MarketDay.timestamp``.
- Fail closed: any exception while building the risk context or reviewing an
  order skips that order (journaled), never fills it.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Generic, SupportsFloat, TypeVar, cast

import numpy as np
import numpy.typing as npt
from scipy.stats import kurtosis as scipy_kurtosis
from scipy.stats import skew as scipy_skew

from optitrade.backtest.metrics import annualized_sharpe, deflated_sharpe_ratio, max_drawdown
from optitrade.core.types import Greeks, OptionType, Order, Portfolio, Position
from optitrade.hedging.band import BandParams
from optitrade.hedging.delta_hedger import DeltaHedger
from optitrade.journal.event_log import EventLog
from optitrade.pricing.black_scholes import bs_greeks_at, bs_price
from optitrade.risk.checks import RiskContext, Verdict
from optitrade.risk.engine import RiskEngine
from optitrade.risk.limits import RiskLimits
from optitrade.strategy.base import MarketDay, Strategy
from optitrade.strategy.costs import IndianOptionsCostModel

_SECONDS_PER_DAY = 86400.0
_SECONDS_PER_YEAR = 365.0 * _SECONDS_PER_DAY  # ACT/365 per ADR-003
_EXPIRY_EPS = 1e-9  # year fraction at/below which a position has expired
_QUANTITY_EPS = 1e-12  # net quantities below this close a position
_MIN_MARK_EXPIRY = 1e-6  # floor when marking near-expiry positions

C = TypeVar("C")


@dataclass(frozen=True)
class BacktestConfig:
    """Typed configuration of the day-loop harness (no magic numbers in flow).

    Attributes:
        risk_limits: Hard pre-trade limits enforced by the risk engine.
        band_params: Whalley-Wilmott no-transaction band for daily hedging.
        cost_model: Itemised option transaction-cost model.
        initial_equity: Starting cash of the account.
        lot_size: Contract lot size strategies are expected to trade; fills
            always honour ``order.contract.lot_size`` — this field is the
            value walk-forward strategy factories should hand to strategies.
        hedge_cost_frac: Proportional cost on underlying hedge fills.
        spread_frac: Full bid-ask spread as a fraction of the order price;
            fills pay half of it against themselves.
        periods_per_year: Periods used to annualise the Sharpe ratio.
        underlying_symbol: Symbol used for hedge orders in the underlying.
    """

    risk_limits: RiskLimits
    band_params: BandParams
    cost_model: IndianOptionsCostModel = field(default_factory=IndianOptionsCostModel)
    initial_equity: float = 1_000_000.0
    lot_size: int = 1
    hedge_cost_frac: float = 5e-4
    spread_frac: float = 0.005
    periods_per_year: int = 252
    underlying_symbol: str = "UNDERLYING"

    def __post_init__(self) -> None:
        if self.initial_equity <= 0.0:
            raise ValueError(f"initial_equity must be positive, got {self.initial_equity}")
        if self.lot_size < 1:
            raise ValueError(f"lot_size must be >= 1, got {self.lot_size}")
        if self.hedge_cost_frac < 0.0:
            raise ValueError(f"hedge_cost_frac must be >= 0, got {self.hedge_cost_frac}")
        if self.spread_frac < 0.0:
            raise ValueError(f"spread_frac must be >= 0, got {self.spread_frac}")
        if self.periods_per_year < 1:
            raise ValueError(f"periods_per_year must be >= 1, got {self.periods_per_year}")


@dataclass(frozen=True)
class BacktestResult:
    """Outcome of one :func:`run_backtest` run.

    ``equity`` and ``daily_pnl`` have one entry per replay day (end-of-day
    marks); ``sharpe`` is annualised; ``max_drawdown`` is a fraction of the
    running peak; ``total_costs`` sums explicit transaction costs (cost-model
    totals plus hedge costs — spread slippage is embedded in fill prices);
    ``n_trades`` counts option fills (hedge fills excluded).
    """

    equity: npt.NDArray[np.float64]
    daily_pnl: npt.NDArray[np.float64]
    sharpe: float
    max_drawdown: float
    total_costs: float
    n_trades: int
    final_equity: float


@dataclass(frozen=True)
class FoldResult(Generic[C]):
    """One walk-forward fold: day-index ranges, chosen config, both Sharpes."""

    fold: int
    train_start: int
    train_stop: int
    test_start: int
    test_stop: int
    chosen_config: C
    train_sharpe: float
    test_sharpe: float
    test_n_trades: int


@dataclass(frozen=True)
class WalkForwardResult(Generic[C]):
    """Stitched out-of-sample outcome across all folds.

    ``oos_sharpe`` is annualised; ``deflated_sharpe`` is the probability (in
    [0, 1]) that the OOS Sharpe exceeds the expected maximum of ``n_trials``
    unskilled trials, per Bailey & López de Prado (2014).
    """

    oos_equity: npt.NDArray[np.float64]
    oos_daily_pnl: npt.NDArray[np.float64]
    oos_sharpe: float
    deflated_sharpe: float
    chosen_configs: tuple[C, ...]
    n_trials: int
    folds: tuple[FoldResult[C], ...]


@dataclass
class _Holding:
    """Internal book entry: the position plus when it was opened."""

    position: Position
    entry_timestamp: float


def _mark_vol(day: MarketDay, strike: float, tau: float) -> float:
    """Marking vol: surface first, then the atm_iv feature, then realized vol."""
    if day.surface is not None:
        return float(cast("SupportsFloat", day.surface.vol(strike, max(tau, _MIN_MARK_EXPIRY))))
    feature = day.features.get("atm_iv")
    return float(feature) if feature is not None else day.realized_vol


def _intrinsic(spot: float, strike: float, option_type: OptionType) -> float:
    if option_type is OptionType.CALL:
        return max(spot - strike, 0.0)
    return max(strike - spot, 0.0)


class _Book:
    """Mutable option book + hedge account state for one backtest run."""

    def __init__(self, cash: float) -> None:
        self.cash = cash
        self.hedge_shares = 0.0
        self.holdings: dict[str, _Holding] = {}

    def tau(self, holding: _Holding, now: float) -> float:
        elapsed = (now - holding.entry_timestamp) / _SECONDS_PER_YEAR
        return holding.position.contract.expiry - elapsed

    def mark_value(self, day: MarketDay) -> float:
        total = 0.0
        for holding in self.holdings.values():
            pos = holding.position
            tau = max(self.tau(holding, day.timestamp), 0.0)
            vol = _mark_vol(day, pos.contract.strike, tau)
            price = float(
                bs_price(
                    day.spot, pos.contract.strike, tau, day.rate, vol, pos.contract.option_type
                )
            )
            total += pos.quantity * pos.contract.lot_size * price
        return total

    def equity(self, day: MarketDay) -> float:
        return self.cash + self.mark_value(day) + self.hedge_shares * day.spot

    def greeks(self, day: MarketDay) -> Greeks:
        """Aggregate book greeks (options scaled by quantity*lot + hedge delta)."""
        total = Greeks(delta=self.hedge_shares)
        for holding in self.holdings.values():
            pos = holding.position
            tau = max(self.tau(holding, day.timestamp), _MIN_MARK_EXPIRY)
            vol = _mark_vol(day, pos.contract.strike, tau)
            per_unit = bs_greeks_at(
                day.spot, pos.contract.strike, tau, day.rate, vol, pos.contract.option_type
            )
            total = total + per_unit.scaled(pos.quantity * pos.contract.lot_size)
        return total

    def positions(self) -> tuple[Position, ...]:
        return tuple(holding.position for holding in self.holdings.values())

    def days_in_trade(self, now: float) -> float:
        if not self.holdings:
            return 0.0
        oldest = min(holding.entry_timestamp for holding in self.holdings.values())
        return (now - oldest) / _SECONDS_PER_DAY

    def apply_option_fill(self, order: Order, fill_price: float, timestamp: float) -> None:
        assert order.contract is not None
        lot = order.contract.lot_size
        self.cash -= order.quantity * fill_price * lot
        existing = self.holdings.get(order.symbol)
        if existing is None:
            self.holdings[order.symbol] = _Holding(
                position=Position(
                    contract=order.contract, quantity=order.quantity, entry_price=fill_price
                ),
                entry_timestamp=timestamp,
            )
            return
        net = existing.position.quantity + order.quantity
        if abs(net) < _QUANTITY_EPS:
            del self.holdings[order.symbol]
        else:
            existing.position = replace(existing.position, quantity=net)


def _order_tau(book: _Book, order: Order, now: float) -> float:
    """Remaining expiry for greeking an order: book age if held, else fresh."""
    assert order.contract is not None
    held = book.holdings.get(order.symbol)
    if held is not None:
        return max(book.tau(held, now), _MIN_MARK_EXPIRY)
    return order.contract.expiry


def run_backtest(
    strategy: Strategy,
    replay: Iterable[MarketDay],
    config: BacktestConfig,
    journal: EventLog | None = None,
) -> BacktestResult:
    """Run one strategy over a day sequence; see the module docstring.

    Per day: expired positions roll off at intrinsic, the strategy decides on
    features augmented with ``days_in_trade``, ENTER/EXIT orders go through
    the risk engine (fail closed; skips journaled), fills pay half-spread
    plus the cost model, then the book is delta-hedged through the
    Whalley-Wilmott band, and end-of-day equity is recorded.
    """
    days = list(replay)
    if not days:
        raise ValueError("replay yielded no days")
    engine = RiskEngine(config.risk_limits, journal=journal)
    hedger = DeltaHedger(config.underlying_symbol, config.band_params)
    book = _Book(cash=config.initial_equity)
    high_water_mark = config.initial_equity
    prev_equity = config.initial_equity
    equity_curve: list[float] = []
    daily_pnl: list[float] = []
    total_costs = 0.0
    n_trades = 0

    for day in days:
        # 1) Expiry roll-off at intrinsic (settlement, not a trade).
        for symbol in list(book.holdings):
            holding = book.holdings[symbol]
            if book.tau(holding, day.timestamp) > _EXPIRY_EPS:
                continue
            pos = holding.position
            payoff = _intrinsic(day.spot, pos.contract.strike, pos.contract.option_type)
            cash_flow = pos.quantity * pos.contract.lot_size * payoff
            book.cash += cash_flow
            del book.holdings[symbol]
            if journal is not None:
                journal.append(
                    "expiry_settlement",
                    {"symbol": symbol, "intrinsic": payoff, "cash_flow": cash_flow},
                )

        # 2) Strategy decision on harness-augmented features.
        augmented = replace(
            day,
            features={**day.features, "days_in_trade": book.days_in_trade(day.timestamp)},
        )
        decision = strategy.decide(augmented, book.positions())

        # 3) Route option orders through the fail-closed risk engine.
        if decision.action in ("enter", "exit"):
            for order in decision.orders:
                filled, cost = _review_and_fill(
                    order, book, day, config, engine, high_water_mark, journal
                )
                if filled:
                    n_trades += 1
                    total_costs += cost

        # 4) Daily delta hedge through the Whalley-Wilmott band.
        if book.holdings or book.hedge_shares != 0.0:
            greeks = book.greeks(day)
            hedge = hedger.decide(
                portfolio_delta=greeks.delta,
                gamma=greeks.gamma,
                spot=day.spot,
                realized_vol=day.realized_vol,
                implied_vol=day.features.get("atm_iv"),
            )
            if hedge.action == "rebalance" and hedge.order is not None:
                traded = hedge.order.quantity
                hedge_cost = config.hedge_cost_frac * abs(traded) * day.spot
                book.cash -= traded * day.spot + hedge_cost
                book.hedge_shares += traded
                total_costs += hedge_cost
                if journal is not None:
                    journal.append(
                        "hedge_fill",
                        {
                            "quantity": traded,
                            "price": day.spot,
                            "cost": hedge_cost,
                            "rationale": hedge.rationale,
                        },
                    )

        # 5) End-of-day mark.
        equity = book.equity(day)
        high_water_mark = max(high_water_mark, equity)
        equity_curve.append(equity)
        daily_pnl.append(equity - prev_equity)
        prev_equity = equity

    equity_arr = np.asarray(equity_curve, dtype=np.float64)
    pnl_arr = np.asarray(daily_pnl, dtype=np.float64)
    result = BacktestResult(
        equity=equity_arr,
        daily_pnl=pnl_arr,
        sharpe=annualized_sharpe(pnl_arr, config.periods_per_year),
        max_drawdown=max_drawdown(equity_arr),
        total_costs=total_costs,
        n_trades=n_trades,
        final_equity=float(equity_arr[-1]),
    )
    if journal is not None:
        journal.append(
            "backtest_result",
            {
                "final_equity": result.final_equity,
                "sharpe": result.sharpe,
                "max_drawdown": result.max_drawdown,
                "total_costs": result.total_costs,
                "n_trades": result.n_trades,
                "n_days": len(days),
            },
        )
    return result


def _review_and_fill(
    order: Order,
    book: _Book,
    day: MarketDay,
    config: BacktestConfig,
    engine: RiskEngine,
    high_water_mark: float,
    journal: EventLog | None,
) -> tuple[bool, float]:
    """Review one option order and fill it if approved; returns (filled, cost).

    Fail closed: any exception in context construction or review skips the
    order. Underlying orders emitted by a strategy (contract=None) are also
    reviewed and pay ``hedge_cost_frac`` instead of the option cost model.
    """
    try:
        equity = book.equity(day)
        if order.contract is not None:
            tau = _order_tau(book, order, day.timestamp)
            vol = _mark_vol(day, order.contract.strike, tau)
            order_greeks = bs_greeks_at(
                day.spot,
                order.contract.strike,
                tau,
                day.rate,
                vol,
                order.contract.option_type,
            ).scaled(order.contract.lot_size)
        else:
            order_greeks = Greeks(delta=1.0)
        ctx = RiskContext(
            portfolio=Portfolio(
                positions=book.positions(),
                cash=book.cash,
                equity=equity,
                high_water_mark=high_water_mark,
                margin_available=equity,
            ),
            portfolio_greeks=book.greeks(day),
            order_greeks=order_greeks,
            margin_required=order.notional,
            spot=day.spot,
        )
        risk_decision = engine.review(order, ctx)
    except Exception as exc:  # fail closed: a broken review never fills
        if journal is not None:
            journal.append(
                "order_skipped",
                {
                    "symbol": order.symbol,
                    "quantity": order.quantity,
                    "reason": f"risk review raised {type(exc).__name__}: {exc}",
                },
            )
        return False, 0.0

    if (
        risk_decision.verdict not in (Verdict.APPROVE, Verdict.RESIZE)
        or risk_decision.adjusted_order is None
    ):
        if journal is not None:
            reasons = "; ".join(
                r.reason for r in risk_decision.results if r.verdict is not Verdict.APPROVE
            )
            journal.append(
                "order_skipped",
                {
                    "symbol": order.symbol,
                    "quantity": order.quantity,
                    "verdict": risk_decision.verdict.value,
                    "reason": reasons or "rejected",
                },
                correlation_id=risk_decision.correlation_id,
            )
        return False, 0.0

    fill = risk_decision.adjusted_order
    half_spread = 0.5 * config.spread_frac
    fill_price = fill.price * (1.0 + half_spread if fill.quantity > 0 else 1.0 - half_spread)
    if fill.contract is not None:
        cost = config.cost_model.cost_of(
            fill_price, fill.quantity, fill.contract.lot_size, is_buy=fill.quantity > 0
        ).total
        book.apply_option_fill(fill, fill_price, day.timestamp)
    else:
        cost = config.hedge_cost_frac * abs(fill.quantity) * fill_price
        book.cash -= fill.quantity * fill_price
        book.hedge_shares += fill.quantity
    book.cash -= cost
    return True, cost


def run_walk_forward(
    strategy_factory: Callable[[C], Strategy],
    param_grid: Sequence[C],
    replay: Iterable[MarketDay],
    config: BacktestConfig,
    n_folds: int = 4,
    train_frac: float = 0.6,
    journal: EventLog | None = None,
) -> WalkForwardResult[C]:
    """Rolling-fold walk-forward evaluation over the full replay history.

    The day sequence is split into ``n_folds`` rolling windows: each fold
    trains on ``train_frac`` of its window (every grid config is backtested
    on the train days; best train Sharpe wins, ties to grid order) and the
    winner alone runs on the fold's held-out test days. Test P&L is stitched
    across folds and ``n_trials = len(param_grid) * n_folds`` — every
    configuration evaluated anywhere — feeds the deflated Sharpe ratio with
    the OOS skew and raw kurtosis (scipy, ``fisher=False``).

    Grid-search and test runs never journal (they would swamp the log);
    only the final ``walk_forward_result`` event is journaled when a journal
    is given.
    """
    if n_folds < 1:
        raise ValueError(f"n_folds must be >= 1, got {n_folds}")
    if not 0.0 < train_frac < 1.0:
        raise ValueError(f"train_frac must be in (0, 1), got {train_frac}")
    if not param_grid:
        raise ValueError("param_grid must be non-empty")
    days = list(replay)
    n = len(days)
    ratio = train_frac / (1.0 - train_frac)
    test_len = int(n / (n_folds + ratio))
    train_len = int(test_len * ratio)
    if test_len < 2 or train_len < 2:
        raise ValueError(
            f"{n} days is too short for {n_folds} folds at train_frac {train_frac} "
            f"(train {train_len}, test {test_len} days per fold)"
        )

    folds: list[FoldResult[C]] = []
    oos_chunks: list[npt.NDArray[np.float64]] = []
    for fold_idx in range(n_folds):
        train_start = fold_idx * test_len
        train_stop = train_start + train_len
        test_stop = train_stop + test_len
        train_days = days[train_start:train_stop]
        test_days = days[train_stop:test_stop]

        best_config = param_grid[0]
        best_sharpe = -math.inf
        for candidate in param_grid:
            train_result = run_backtest(strategy_factory(candidate), train_days, config)
            if train_result.sharpe > best_sharpe:
                best_sharpe = train_result.sharpe
                best_config = candidate
        test_result = run_backtest(strategy_factory(best_config), test_days, config)
        oos_chunks.append(test_result.daily_pnl)
        folds.append(
            FoldResult(
                fold=fold_idx,
                train_start=train_start,
                train_stop=train_stop,
                test_start=train_stop,
                test_stop=test_stop,
                chosen_config=best_config,
                train_sharpe=best_sharpe,
                test_sharpe=test_result.sharpe,
                test_n_trades=test_result.n_trades,
            )
        )

    oos_pnl = np.concatenate(oos_chunks)
    n_trials = len(param_grid) * n_folds
    oos_std = float(np.std(oos_pnl, ddof=1)) if oos_pnl.size > 1 else 0.0
    if oos_std > 0.0:
        sr_per_period = float(np.mean(oos_pnl)) / oos_std
        deflated = deflated_sharpe_ratio(
            observed_sr=sr_per_period,
            n_trials=n_trials,
            n_obs=int(oos_pnl.size),
            skew=float(scipy_skew(oos_pnl)),
            kurtosis=float(scipy_kurtosis(oos_pnl, fisher=False)),
        )
    else:
        # Degenerate zero-variance OOS P&L: no evidence of skill.
        deflated = 0.0
    result = WalkForwardResult(
        oos_equity=config.initial_equity + np.cumsum(oos_pnl),
        oos_daily_pnl=oos_pnl,
        oos_sharpe=annualized_sharpe(oos_pnl, config.periods_per_year),
        deflated_sharpe=deflated,
        chosen_configs=tuple(fold.chosen_config for fold in folds),
        n_trials=n_trials,
        folds=tuple(folds),
    )
    if journal is not None:
        journal.append(
            "walk_forward_result",
            {
                "oos_sharpe": result.oos_sharpe,
                "deflated_sharpe": result.deflated_sharpe,
                "n_trials": result.n_trials,
                "n_oos_days": int(oos_pnl.size),
                "folds": [
                    {
                        "fold": fold.fold,
                        "train": [fold.train_start, fold.train_stop],
                        "test": [fold.test_start, fold.test_stop],
                        "chosen_config": repr(fold.chosen_config),
                        "train_sharpe": fold.train_sharpe,
                        "test_sharpe": fold.test_sharpe,
                        "test_n_trades": fold.test_n_trades,
                    }
                    for fold in folds
                ],
            },
        )
    return result


__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "FoldResult",
    "WalkForwardResult",
    "run_backtest",
    "run_walk_forward",
]
