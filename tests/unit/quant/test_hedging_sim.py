"""Economic ground-truth tests for the delta-hedging Monte-Carlo harness.

All runs are seeded (seed=7) and use 64 GBM paths x 126 daily steps on an ATM
call (strike 100, expiry 0.75y, IV 20%, r=0), hedging 100 options with a
near-zero band (min_half_width=1e-6) to force near-continuous rebalancing.
"""

import numpy as np
import pytest

from optitrade.backtest import run_delta_hedge_sim, simulate_gbm_paths
from optitrade.core import OptionContract, OptionType
from optitrade.hedging import BandParams, DeltaHedger

pytestmark = pytest.mark.unit

SPOT = 100.0
RATE = 0.0
IMPLIED_VOL = 0.2
DT = 1.0 / 252.0
N_STEPS = 126
N_PATHS = 64
SEED = 7
QUANTITY = 100.0

OPTION = OptionContract(symbol="TEST100C", strike=100.0, expiry=0.75, option_type=OptionType.CALL)
# proportional_cost=0 collapses the WW band onto min_half_width -> the hedger
# rebalances every step that the whole-share rounding allows.
FREE_HEDGER = DeltaHedger(
    "TEST", BandParams(proportional_cost=0.0, risk_aversion=1.0, min_half_width=1e-6)
)
COSTLY_HEDGER = DeltaHedger(
    "TEST", BandParams(proportional_cost=5e-4, risk_aversion=1.0, min_half_width=1e-6)
)


def run(realized_vol, hedger):
    return run_delta_hedge_sim(
        option=OPTION,
        implied_vol=IMPLIED_VOL,
        realized_vol=realized_vol,
        spot=SPOT,
        rate=RATE,
        hedger=hedger,
        dt=DT,
        n_steps=N_STEPS,
        n_paths=N_PATHS,
        seed=SEED,
        quantity=QUANTITY,
    )


class TestGbmPaths:
    def test_shape_start_and_determinism(self):
        paths = simulate_gbm_paths(SPOT, 0.05, 0.2, DT, 10, 8, seed=123)
        assert paths.shape == (8, 11)
        assert np.all(paths[:, 0] == SPOT)
        assert np.all(paths > 0.0)
        again = simulate_gbm_paths(SPOT, 0.05, 0.2, DT, 10, 8, seed=123)
        np.testing.assert_array_equal(paths, again)

    def test_terminal_moments_match_gbm(self):
        drift, vol, n = 0.05, 0.2, 4000
        paths = simulate_gbm_paths(SPOT, drift, vol, DT, 126, n, seed=99)
        log_ret = np.log(paths[:, -1] / SPOT)
        horizon = 126 * DT
        assert log_ret.mean() == pytest.approx((drift - 0.5 * vol**2) * horizon, abs=0.01)
        assert log_ret.std(ddof=1) == pytest.approx(vol * np.sqrt(horizon), rel=0.05)


class TestRealizedEqualsImplied:
    def test_mean_pnl_near_zero_and_theta_tracked(self):
        """Ground truth (a): at realized == implied with zero costs the hedged
        book neither makes nor loses money and gamma harvest tracks theta.

        Calibration at seed=7, 64 paths x 126 steps: mean P&L = -5.19 with
        std = 32.87 (std error 4.11, i.e. mean is 1.26 std errors from zero),
        theta_tracking = 0.0236. Asserted with ~2x headroom: |mean| < 2 std
        errors, theta_tracking < 0.05.
        """
        result = run(realized_vol=IMPLIED_VOL, hedger=FREE_HEDGER)
        std_error = result.std_pnl / np.sqrt(N_PATHS)
        assert abs(result.mean_pnl) < 2.0 * std_error
        assert result.theta_tracking < 0.05
        assert result.mean_costs == 0.0

    def test_band_forces_near_continuous_rebalancing(self):
        result = run(realized_vol=IMPLIED_VOL, hedger=FREE_HEDGER)
        # Calibrated: 86.1 rebalances of 126 steps on average; the remainder
        # are sub-share breaches that round to zero and hold.
        assert result.n_rebalances_mean > 0.5 * N_STEPS
        assert result.final_pnl.shape == (N_PATHS,)
        assert len(result.decision_counts) == N_PATHS
        for counts in result.decision_counts:
            assert counts["hold"] + counts["rebalance"] == N_STEPS

    def test_deterministic_for_fixed_seed(self):
        first = run(realized_vol=IMPLIED_VOL, hedger=FREE_HEDGER)
        second = run(realized_vol=IMPLIED_VOL, hedger=FREE_HEDGER)
        np.testing.assert_array_equal(first.final_pnl, second.final_pnl)
        assert first.theta_tracking == second.theta_tracking


class TestRealizedAboveImplied:
    def test_long_gamma_earns_with_zero_costs(self):
        """Ground truth (b): realized 30% vs implied 20% with zero costs.

        A delta-hedged long option earns ~0.5*Gamma*S^2*(rv^2 - iv^2)*dt per
        step. Calibrated mean P&L = +221.2 (std 97.0), >> 2 std errors.
        """
        result = run(realized_vol=0.3, hedger=FREE_HEDGER)
        assert result.mean_pnl > 0.0
        assert result.mean_pnl > 2.0 * result.std_pnl / np.sqrt(N_PATHS)
        assert result.mean_costs == 0.0


class TestTransactionCosts:
    def test_costs_accrue_and_drag_pnl(self):
        """Ground truth (c): 5bp proportional costs. Calibrated: mean costs
        = 17.3 per path, mean P&L 203.8 vs 221.2 without costs."""
        free = run(realized_vol=0.3, hedger=FREE_HEDGER)
        costly = run(realized_vol=0.3, hedger=COSTLY_HEDGER)
        assert costly.mean_costs > 0.0
        assert costly.mean_pnl < free.mean_pnl


class TestValidation:
    def test_horizon_beyond_expiry_rejected(self):
        short_dated = OptionContract(
            symbol="TEST100C", strike=100.0, expiry=0.1, option_type=OptionType.CALL
        )
        with pytest.raises(ValueError, match="expiry"):
            run_delta_hedge_sim(
                option=short_dated,
                implied_vol=IMPLIED_VOL,
                realized_vol=IMPLIED_VOL,
                spot=SPOT,
                rate=RATE,
                hedger=FREE_HEDGER,
                dt=DT,
                n_steps=N_STEPS,
                n_paths=4,
                seed=SEED,
            )
