"""Economic ground-truth tests for the VRP strategy + walk-forward harness.

All runs are seeded on a 200-day :class:`SyntheticVRPMarket` (spot 100,
rv 18%, r 5%, lot 25). Calibration at seed=11:

- vrp=+0.06, zero costs: final equity 1,000,224.81, mean daily P&L +1.12,
  14 option fills (~7 monthly straddle cycles rolling off at expiry).
- vrp=0.0, same seed: 0 trades (the AR(1) IV noise, stationary std ~0.83
  vol points, never crosses the 3-vol-point entry gate).
- Real Indian costs + 50bp spread + hedge costs: final 999,862.37
  (explicit costs 360.00) — the cost drag is the point of test (c).
- Walk-forward (2 folds x 3 configs): OOS Sharpe 6.16 annualised, deflated
  Sharpe 0.9951 < raw Phi(SR*sqrt(n)) 0.99998.
"""

import numpy as np
import pytest
from scipy.stats import norm

from optitrade.backtest import (
    BacktestConfig,
    SyntheticVRPMarket,
    annualized_sharpe,
    max_drawdown,
    min_days_for_walk_forward,
    run_backtest,
    run_walk_forward,
    turnover,
)
from optitrade.hedging import BandParams
from optitrade.journal import EventLog
from optitrade.risk import RiskLimits
from optitrade.strategy import IndianCostRates, IndianOptionsCostModel, VRPConfig, VRPStrategy

pytestmark = pytest.mark.unit

N_DAYS = 200
SEED = 11
LOT = 25
REALIZED_VOL = 0.18

ZERO_RATES = IndianCostRates(
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


def make_market(vrp, seed=SEED):
    return SyntheticVRPMarket(N_DAYS, realized_vol=REALIZED_VOL, vrp=vrp, seed=seed)


def free_config(**overrides):
    kwargs = dict(
        risk_limits=PERMISSIVE_LIMITS,
        band_params=BAND,
        cost_model=IndianOptionsCostModel(ZERO_RATES),
        lot_size=LOT,
        hedge_cost_frac=0.0,
        spread_frac=0.0,
    )
    kwargs.update(overrides)
    return BacktestConfig(**kwargs)


def free_strategy(config=None):
    return VRPStrategy(config, cost_model=IndianOptionsCostModel(ZERO_RATES), lot_size=LOT)


class TestBacktestEconomics:
    def test_positive_vrp_zero_costs_is_profitable(self):
        """Ground truth (a): a genuine +6 vol-point VRP with no frictions
        must be harvested — positive mean daily P&L and equity growth."""
        result = run_backtest(free_strategy(), make_market(vrp=0.06), free_config())
        assert float(np.mean(result.daily_pnl)) > 0.0
        assert result.final_equity > 1_000_000.0
        assert result.n_trades >= 4  # several monthly straddle cycles
        assert result.equity.shape == (N_DAYS,)
        assert result.total_costs == 0.0

    def test_zero_vrp_rarely_or_never_trades(self):
        """Ground truth (b): with no premium in IV the entry gate should
        essentially never trigger (noise std ~0.83 vol pts vs 3 pt gate)."""
        result = run_backtest(free_strategy(), make_market(vrp=0.0), free_config())
        assert result.n_trades <= 4
        assert result.final_equity == pytest.approx(1_000_000.0, rel=1e-3)

    def test_costs_drag_final_equity(self):
        """Ground truth (c): identical market, real Indian costs + spread +
        hedge frictions on -> strictly poorer outcome."""
        market = make_market(vrp=0.06)
        free = run_backtest(free_strategy(), market, free_config())
        costly = run_backtest(
            VRPStrategy(lot_size=LOT),
            market,
            BacktestConfig(
                risk_limits=PERMISSIVE_LIMITS,
                band_params=BAND,
                cost_model=IndianOptionsCostModel(),
                lot_size=LOT,
            ),
        )
        assert costly.total_costs > 0.0
        assert costly.final_equity < free.final_equity

    def test_deterministic_for_fixed_seed(self):
        first = run_backtest(free_strategy(), make_market(vrp=0.06), free_config())
        second = run_backtest(free_strategy(), make_market(vrp=0.06), free_config())
        np.testing.assert_array_equal(first.equity, second.equity)
        assert first.n_trades == second.n_trades

    def test_fail_closed_risk_limits_block_all_entries(self):
        """A vega cap far below one straddle's vega must block every entry:
        no fills, flat equity — rejection observable at the boundary."""
        tight = RiskLimits(
            max_abs_delta=1e9,
            max_abs_gamma=1e9,
            max_abs_vega=1.0,
            max_drawdown=1.0,
            max_concentration=1.0,
        )
        result = run_backtest(
            free_strategy(), make_market(vrp=0.06), free_config(risk_limits=tight)
        )
        assert result.n_trades == 0
        assert result.final_equity == pytest.approx(1_000_000.0)


class TestWalkForward:
    GRID = (
        VRPConfig(entry_vrp_min=0.02),
        VRPConfig(entry_vrp_min=0.04),
        VRPConfig(entry_vrp_min=0.08),
    )

    def run_wf(self, journal=None):
        return run_walk_forward(
            free_strategy,
            self.GRID,
            make_market(vrp=0.06),
            free_config(),
            n_folds=2,
            train_frac=0.6,
            journal=journal,
        )

    def test_deflated_sharpe_is_a_discounted_probability(self):
        """Ground truth (d): DSR in [0, 1], n_trials = grid x folds, and the
        deflation bites: DSR < naive Phi(per-period SR * sqrt(n_obs))."""
        result = self.run_wf()
        assert 0.0 <= result.deflated_sharpe <= 1.0
        assert result.n_trials == len(self.GRID) * 2
        pnl = result.oos_daily_pnl
        sr_per_period = float(np.mean(pnl)) / float(np.std(pnl, ddof=1))
        naive = float(norm.cdf(sr_per_period * np.sqrt(pnl.size)))
        assert result.deflated_sharpe < naive
        assert result.oos_sharpe > 0.0  # the premium is real in this world

    def test_fold_table_and_stitching_are_consistent(self, tmp_path):
        journal = EventLog(tmp_path, "wf")
        result = self.run_wf(journal=journal)
        assert len(result.folds) == 2
        assert len(result.chosen_configs) == 2
        n_oos = sum(fold.test_stop - fold.test_start for fold in result.folds)
        assert result.oos_daily_pnl.shape == (n_oos,)
        assert result.oos_equity.shape == (n_oos,)
        for fold in result.folds:
            assert fold.train_stop == fold.test_start  # test follows train
            assert fold.chosen_config in self.GRID
        events = [e for e in journal.replay() if e.event_type == "walk_forward_result"]
        assert len(events) == 1
        assert events[0].data["n_trials"] == result.n_trials

    def test_walk_forward_is_deterministic(self):
        first = self.run_wf()
        second = self.run_wf()
        np.testing.assert_array_equal(first.oos_daily_pnl, second.oos_daily_pnl)
        assert first.deflated_sharpe == second.deflated_sharpe


class TestMinDaysForWalkForward:
    """``min_days_for_walk_forward`` must agree with the harness it predicts.

    A live capture accumulating history asks "how many more days?"; answering
    by catching the ``ValueError`` would cost a full replay build. These tests
    pin the prediction to the real fold arithmetic so the two cannot drift.
    """

    GRID = (VRPConfig(entry_vrp_min=0.04),)
    SETTINGS = tuple(
        (n_folds, train_frac) for n_folds in (1, 2, 3, 4, 6) for train_frac in (0.5, 0.6, 0.75, 0.8)
    )

    @pytest.mark.parametrize(("n_folds", "train_frac"), SETTINGS)
    def test_min_days_matches_run_walk_forward(self, n_folds, train_frac):
        """At the predicted count the harness runs; one day fewer it raises."""
        predicted = min_days_for_walk_forward(n_folds, train_frac)
        days = list(make_market(vrp=0.06))
        assert predicted <= len(days), "widen the synthetic market for this setting"

        run_walk_forward(
            free_strategy, self.GRID, days[:predicted], free_config(), n_folds, train_frac
        )
        with pytest.raises(ValueError, match="too short"):
            run_walk_forward(
                free_strategy,
                self.GRID,
                days[: predicted - 1],
                free_config(),
                n_folds,
                train_frac,
            )

    def test_default_settings_need_eleven_days(self):
        """Pins the number the frontend shows as "N days needed"."""
        assert min_days_for_walk_forward() == 11
        assert min_days_for_walk_forward(n_folds=4, train_frac=0.6) == 11

    def test_more_folds_need_more_days(self):
        assert min_days_for_walk_forward(2, 0.6) < min_days_for_walk_forward(8, 0.6)

    def test_rejects_the_same_arguments_run_walk_forward_rejects(self):
        with pytest.raises(ValueError, match="n_folds"):
            min_days_for_walk_forward(0, 0.6)
        for bad in (0.0, 1.0, -0.1):
            with pytest.raises(ValueError, match="train_frac"):
                min_days_for_walk_forward(4, bad)


class TestMetricGuards:
    def test_annualized_sharpe_zero_variance_returns_zero(self):
        assert annualized_sharpe(np.zeros(10)) == 0.0
        assert annualized_sharpe([1.0]) == 0.0

    def test_max_drawdown_hand_case(self):
        # Peak 120 -> trough 90 is a 25% drawdown.
        equity = np.array([100.0, 120.0, 90.0, 110.0])
        assert max_drawdown(equity) == pytest.approx(0.25)
        assert max_drawdown(np.array([1.0, 2.0, 3.0])) == 0.0

    def test_turnover_hand_case(self):
        assert turnover([100.0, -50.0], 300.0) == pytest.approx(0.5)
        with pytest.raises(ValueError, match="equity_mean"):
            turnover([1.0], 0.0)
