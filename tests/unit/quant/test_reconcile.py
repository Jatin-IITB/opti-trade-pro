"""Backtest-vs-desk drift metric tests (phase-4 exit metric).

Ground truths:
- A do-nothing strategy produces zero P&L in both execution models, so the
  drift is exactly 0 bps and the correlation guard returns 0.0 (degenerate).
- An active VRP strategy on a positive-VRP synthetic market trades in both
  models; the report's lengths line up with the replay and every statistic
  is finite, with drift > 0 because the two execution models genuinely
  differ (hedge booking, book aging — documented in desk/reconcile.py).
"""

import numpy as np
import pytest

from optitrade.backtest import BacktestConfig, SyntheticVRPMarket
from optitrade.desk import DeskConfig
from optitrade.desk.reconcile import DriftReport, backtest_vs_desk_drift
from optitrade.hedging import BandParams
from optitrade.journal import EventLog
from optitrade.risk import RiskLimits
from optitrade.strategy import (
    IndianCostRates,
    IndianOptionsCostModel,
    StrategyDecision,
    VRPConfig,
    VRPStrategy,
)

pytestmark = pytest.mark.unit

INITIAL_EQUITY = 1_000_000.0
LOT = 25
SEED = 11

FEE_FREE = IndianCostRates(
    brokerage_per_order=0.0,
    stt_sell_frac=0.0,
    exchange_txn_frac=0.0,
    gst_frac=0.0,
    sebi_frac=0.0,
    stamp_buy_frac=0.0,
    hedge_cost_frac=0.0,
)
PERMISSIVE_LIMITS = RiskLimits(
    max_abs_delta=1e9,
    max_abs_gamma=1e9,
    max_abs_vega=1e12,
    max_drawdown=1.0,
    max_concentration=1.0,
    margin_buffer=1.0,
)
BAND = BandParams(proportional_cost=0.0, risk_aversion=1.0, min_half_width=1.0, max_half_width=5.0)

BT_CONFIG = BacktestConfig(
    risk_limits=PERMISSIVE_LIMITS,
    band_params=BAND,
    cost_model=IndianOptionsCostModel(FEE_FREE),
    lot_size=LOT,
    hedge_cost_frac=0.0,
    spread_frac=0.0,
)
DESK_CONFIG = DeskConfig(
    limits=PERMISSIVE_LIMITS,
    band=BAND,
    underlying_symbol="UNDERLYING",
    spread_frac=0.0,
    require_debate=False,
)


class HoldStrategy:
    """Strategy-protocol stub that never trades."""

    @property
    def name(self) -> str:
        return "hold"

    def decide(self, day, open_positions) -> StrategyDecision:
        return StrategyDecision(action="hold", thesis="do nothing, by design")


def vrp_factory() -> VRPStrategy:
    return VRPStrategy(
        VRPConfig(entry_vrp_min=0.02),
        cost_model=IndianOptionsCostModel(FEE_FREE),
        lot_size=LOT,
    )


def make_days(n_days: int, vrp: float = 0.06) -> list:
    return list(SyntheticVRPMarket(n_days, realized_vol=0.18, vrp=vrp, seed=SEED))


class TestDoNothingSanity:
    def test_zero_trades_means_zero_drift(self, tmp_path):
        days = make_days(10)
        report = backtest_vs_desk_drift(
            days,
            HoldStrategy,
            BT_CONFIG,
            DESK_CONFIG,
            INITIAL_EQUITY,
            kill_switch_path=tmp_path / "HALT",
        )
        assert report.n_days == 10
        assert report.backtest_daily_pnl.shape == (10,)
        assert report.desk_daily_pnl.shape == (10,)
        np.testing.assert_allclose(report.backtest_daily_pnl, 0.0, atol=1e-9)
        np.testing.assert_allclose(report.desk_daily_pnl, 0.0, atol=1e-9)
        assert report.mean_abs_drift_bps == pytest.approx(0.0, abs=1e-12)
        assert report.max_abs_drift_bps == pytest.approx(0.0, abs=1e-12)
        assert report.correlation == 0.0  # degenerate-series guard, not NaN
        assert "0.000 bps" in report.summary()


class TestActiveStrategyDrift:
    def run_drift(self, tmp_path, journal=None) -> DriftReport:
        return backtest_vs_desk_drift(
            make_days(40),
            vrp_factory,
            BT_CONFIG,
            DESK_CONFIG,
            INITIAL_EQUITY,
            kill_switch_path=tmp_path / "HALT",
            journal=journal,
        )

    def test_report_lengths_and_finiteness(self, tmp_path):
        days = make_days(40)
        report = self.run_drift(tmp_path)
        assert report.n_days == 40
        assert report.backtest_daily_pnl.shape == (40,)
        assert report.desk_daily_pnl.shape == (40,)
        assert len(report.per_day) == 40
        assert [ts for ts, _, _ in report.per_day] == [d.timestamp for d in days]
        for _ts, bt_pnl, desk_pnl in report.per_day:
            assert np.isfinite(bt_pnl) and np.isfinite(desk_pnl)
        assert np.isfinite(report.mean_abs_drift_bps)
        assert np.isfinite(report.max_abs_drift_bps)
        assert -1.0 <= report.correlation <= 1.0
        # Both execution models actually traded the planted premium...
        assert np.any(report.backtest_daily_pnl != 0.0)
        assert np.any(report.desk_daily_pnl != 0.0)
        # ...and the two models genuinely differ (hedge booking, book aging),
        # so the residual drift is non-zero — that gap is what the metric is for.
        assert report.max_abs_drift_bps > 0.0
        assert report.mean_abs_drift_bps <= report.max_abs_drift_bps

    def test_drift_is_deterministic(self, tmp_path):
        first = self.run_drift(tmp_path / "a")
        second = self.run_drift(tmp_path / "b")
        np.testing.assert_array_equal(first.backtest_daily_pnl, second.backtest_daily_pnl)
        np.testing.assert_array_equal(first.desk_daily_pnl, second.desk_daily_pnl)
        assert first.mean_abs_drift_bps == second.mean_abs_drift_bps

    def test_journals_drift_report_when_journal_given(self, tmp_path):
        journal = EventLog(tmp_path, "drift-run")
        report = self.run_drift(tmp_path, journal=journal)
        events = [e for e in journal.replay() if e.event_type == "drift_report"]
        assert len(events) == 1
        assert events[0].data["n_days"] == report.n_days
        assert events[0].data["mean_abs_drift_bps"] == report.mean_abs_drift_bps
        # The desk's per-day money path was journaled too (ADR-009).
        assert any(e.event_type == "daily_cycle" for e in journal.replay())


class TestValidation:
    def test_empty_replay_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="no days"):
            backtest_vs_desk_drift(
                [],
                HoldStrategy,
                BT_CONFIG,
                DESK_CONFIG,
                INITIAL_EQUITY,
                kill_switch_path=tmp_path / "HALT",
            )

    def test_non_positive_equity_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="initial_equity"):
            backtest_vs_desk_drift(
                make_days(5),
                HoldStrategy,
                BT_CONFIG,
                DESK_CONFIG,
                0.0,
                kill_switch_path=tmp_path / "HALT",
            )
