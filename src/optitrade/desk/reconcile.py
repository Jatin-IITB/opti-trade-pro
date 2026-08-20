"""Backtest-vs-desk drift reconciliation — the phase-4 exit metric.

:func:`backtest_vs_desk_drift` runs *one* strategy protocol over *one*
materialised day sequence through both execution models — the walk-forward
backtester (:func:`~optitrade.backtest.walk_forward.run_backtest`) and the
daily desk cycle (:func:`~optitrade.desk.cycle.run_daily_cycle`) — and
reports the daily P&L divergence in bps of initial equity. Because the
strategy, the surfaces and the day ordering are held identical by
construction, the residual drift isolates execution-model differences:

- fill accounting (the backtester's half-spread + itemised cost model vs the
  desk's spread-adjusted paper fills),
- margin treatment (the backtester lends the whole equity as margin; the
  desk runs on a fixed margin pool),
- hedge booking (the backtester books underlying hedge fills; the paper
  desk journals hedge *decisions* without booking them), and
- book aging (the backtester decays positions to expiry and settles them at
  intrinsic; the paper desk marks at construction expiry — cycle.py's
  documented "no book aging" scope).

A small, explained drift is the acceptance evidence that the desk executes
what the backtest promised; a large one names the execution gap to close.

Judgment calls (documented, not hidden):

- The backtest runs with ``bt_config``'s ``initial_equity`` replaced by the
  ``initial_equity`` argument so both books start from the same base —
  drift in bps of two different equity bases would be meaningless.
- The desk loop runs ``panel=None``: the backtester has no debate panel, so
  governance review must be off on both sides for parity (the fail-closed
  risk engine still reviews every order in both).
- One fresh ``strategy_factory()`` instance per execution model, so no
  state can leak from the backtest run into the desk run; strategies are
  stateless by contract (strategy/base.py), so per-model — not per-day —
  freshness matches how ``run_backtest`` itself consumes a strategy.
- The desk cycle mandates journaling (ADR-009); when the caller supplies no
  journal, desk events go to a scratch :class:`EventLog` next to the kill
  switch (``kill_switch_path.parent``). The backtest side runs unjournaled
  (its result is embedded in the report), mirroring how walk-forward grid
  runs stay out of the log.
- A pre-engaged kill switch at ``kill_switch_path`` is honoured, not reset:
  the desk skipping every day *is* the desk's behaviour (fail closed), and
  the resulting drift reports it.
- ``correlation`` is 0.0 when either P&L series is degenerate (fewer than
  two days or zero variance): "no measurable linear relationship", never a
  NaN.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import numpy.typing as npt

from optitrade.backtest.walk_forward import BacktestConfig, run_backtest
from optitrade.core.types import Portfolio, Position
from optitrade.desk.cycle import DeskConfig, run_daily_cycle
from optitrade.desk.kill_switch import KillSwitch
from optitrade.journal.event_log import EventLog
from optitrade.strategy.base import MarketDay, Strategy

# The desk's fixed margin pool as a fraction of initial equity: a deliberately
# conservative proxy against the backtester's margin_available = equity, so
# margin-model divergence is part of the measured drift (see module doc).
_DESK_MARGIN_FRACTION = 0.5
_BPS_PER_UNIT = 1e4
_SCRATCH_JOURNAL_RUN_ID = "reconcile-desk"


@dataclass(frozen=True)
class DriftReport:
    """Day-by-day P&L divergence between the backtester and the paper desk.

    ``mean_abs_drift_bps`` / ``max_abs_drift_bps`` measure
    ``|desk - backtest|`` daily P&L in bps of initial equity;
    ``correlation`` is the Pearson correlation of the two daily P&L series
    (0.0 when degenerate); ``per_day`` holds
    ``(timestamp, backtest_pnl, desk_pnl)`` per replay day.
    """

    n_days: int
    backtest_daily_pnl: npt.NDArray[np.float64]
    desk_daily_pnl: npt.NDArray[np.float64]
    mean_abs_drift_bps: float
    max_abs_drift_bps: float
    correlation: float
    per_day: tuple[tuple[float, float, float], ...]

    def summary(self) -> str:
        """Plain-text summary carrying the numbers, for reports and logs."""
        return (
            f"backtest-vs-desk drift over {self.n_days} days: "
            f"backtest total P&L {float(np.sum(self.backtest_daily_pnl)):+.2f}, "
            f"desk total P&L {float(np.sum(self.desk_daily_pnl)):+.2f}; "
            f"mean |drift| {self.mean_abs_drift_bps:.3f} bps of initial equity, "
            f"max |drift| {self.max_abs_drift_bps:.3f} bps, "
            f"daily P&L correlation {self.correlation:+.4f}"
        )


def backtest_vs_desk_drift(
    replay: Iterable[MarketDay],
    strategy_factory: Callable[[], Strategy],
    bt_config: BacktestConfig,
    desk_config: DeskConfig,
    initial_equity: float,
    kill_switch_path: Path,
    journal: EventLog | None = None,
) -> DriftReport:
    """Run the same strategy and days through backtester and desk; report drift.

    ``replay`` is materialised exactly once so both execution models see the
    identical day list. Desk daily P&L is the diff of end-of-cycle equities
    from ``initial_equity``. Appends a ``drift_report`` event when
    ``journal`` is given.
    """
    if initial_equity <= 0.0:
        raise ValueError(f"initial_equity must be positive, got {initial_equity}")
    days = list(replay)
    if not days:
        raise ValueError("replay yielded no days")

    # (a) Backtester, rebased to the shared initial equity (see module doc).
    bt_result = run_backtest(
        strategy_factory(), days, replace(bt_config, initial_equity=initial_equity)
    )

    # (b) Desk loop: fresh portfolio, fresh strategy, no panel (parity).
    desk_journal = (
        journal
        if journal is not None
        else EventLog(kill_switch_path.parent, _SCRATCH_JOURNAL_RUN_ID)
    )
    kill_switch = KillSwitch(kill_switch_path)
    strategy = strategy_factory()
    portfolio = Portfolio(
        cash=initial_equity,
        equity=initial_equity,
        high_water_mark=initial_equity,
        margin_available=initial_equity * _DESK_MARGIN_FRACTION,
    )
    book: tuple[Position, ...] = ()
    desk_equities: list[float] = []
    for day in days:
        _, book, portfolio = run_daily_cycle(
            day=day,
            portfolio=portfolio,
            book=book,
            strategy=strategy,
            config=desk_config,
            journal=desk_journal,
            kill_switch=kill_switch,
            panel=None,
        )
        desk_equities.append(portfolio.equity)

    bt_pnl = np.asarray(bt_result.daily_pnl, dtype=np.float64)
    desk_pnl = np.diff(np.concatenate(([initial_equity], desk_equities)))
    drift_bps = (desk_pnl - bt_pnl) / initial_equity * _BPS_PER_UNIT

    if bt_pnl.size < 2 or float(np.std(bt_pnl)) == 0.0 or float(np.std(desk_pnl)) == 0.0:
        correlation = 0.0  # degenerate: no measurable linear relationship
    else:
        correlation = float(np.corrcoef(bt_pnl, desk_pnl)[0, 1])

    report = DriftReport(
        n_days=len(days),
        backtest_daily_pnl=bt_pnl,
        desk_daily_pnl=desk_pnl,
        mean_abs_drift_bps=float(np.mean(np.abs(drift_bps))),
        max_abs_drift_bps=float(np.max(np.abs(drift_bps))),
        correlation=correlation,
        per_day=tuple(
            (float(day.timestamp), float(b), float(d))
            for day, b, d in zip(days, bt_pnl, desk_pnl, strict=True)
        ),
    )
    if journal is not None:
        journal.append(
            "drift_report",
            {
                "n_days": report.n_days,
                "mean_abs_drift_bps": report.mean_abs_drift_bps,
                "max_abs_drift_bps": report.max_abs_drift_bps,
                "correlation": report.correlation,
                "backtest_total_pnl": float(np.sum(bt_pnl)),
                "desk_total_pnl": float(np.sum(desk_pnl)),
                "summary": report.summary(),
            },
        )
    return report


__all__ = ["DriftReport", "backtest_vs_desk_drift"]
