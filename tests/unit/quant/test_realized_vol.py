"""Ground-truth tests for the OHLC realized-vol estimators.

Bars are synthesised from seeded GBM at a known vol: each daily bar
aggregates ``N_SUB`` fine sub-steps, so highs/lows are discretely monitored.
That biases the range-based estimators (Parkinson, Garman-Klass) low by
~0.73/sqrt(N_SUB) (Broadie-Glasserman continuity correction), which is ~6%
at N_SUB=128 — well inside the 15% accuracy gate but the reason N_SUB is
large here.
"""

import numpy as np
import pytest

from optitrade.backtest import simulate_gbm_paths
from optitrade.vol import close_to_close_vol, garman_klass_vol, parkinson_vol

pytestmark = pytest.mark.unit

TRUE_VOL = 0.2
N_DAYS = 250
N_SUB = 128


def make_ohlc(seed, n_days=N_DAYS, n_sub=N_SUB, vol=TRUE_VOL):
    """Daily OHLC bars aggregated from n_sub GBM sub-steps per day."""
    dt = 1.0 / (252 * n_sub)
    path = simulate_gbm_paths(100.0, 0.0, vol, dt, n_days * n_sub, 1, seed)[0]
    starts = np.arange(n_days) * n_sub
    opens = path[starts]
    closes = path[starts + n_sub]
    body = path[: n_days * n_sub]
    highs = np.maximum(np.maximum.reduceat(body, starts), closes)
    lows = np.minimum(np.minimum.reduceat(body, starts), closes)
    return opens, highs, lows, closes


class TestAccuracy:
    def test_close_to_close_within_15_percent(self):
        _, _, _, closes = make_ohlc(seed=42)
        assert close_to_close_vol(closes) == pytest.approx(TRUE_VOL, rel=0.15)

    def test_parkinson_within_15_percent(self):
        _, highs, lows, _ = make_ohlc(seed=42)
        assert parkinson_vol(highs, lows) == pytest.approx(TRUE_VOL, rel=0.15)

    def test_garman_klass_within_15_percent(self):
        opens, highs, lows, closes = make_ohlc(seed=42)
        assert garman_klass_vol(opens, highs, lows, closes) == pytest.approx(TRUE_VOL, rel=0.15)

    def test_deterministic_for_fixed_seed(self):
        first = make_ohlc(seed=7)
        second = make_ohlc(seed=7)
        assert garman_klass_vol(*first) == garman_klass_vol(*second)
        assert close_to_close_vol(first[3]) == close_to_close_vol(second[3])


class TestEfficiency:
    def test_garman_klass_tighter_than_close_to_close_across_seeds(self):
        """GK is ~7.4x more efficient than close-to-close under GBM, so its
        estimates disperse far less across independent short samples."""
        cc, gk = [], []
        for seed in range(16):
            opens, highs, lows, closes = make_ohlc(seed=seed, n_days=64, n_sub=64)
            cc.append(close_to_close_vol(closes))
            gk.append(garman_klass_vol(opens, highs, lows, closes))
        assert np.std(gk, ddof=1) < np.std(cc, ddof=1)


class TestValidation:
    def test_close_to_close_needs_three_closes(self):
        with pytest.raises(ValueError, match="closes"):
            close_to_close_vol([100.0, 101.0])

    def test_non_positive_prices_rejected(self):
        with pytest.raises(ValueError, match="positive"):
            close_to_close_vol([100.0, 0.0, 101.0])
        with pytest.raises(ValueError, match="positive"):
            parkinson_vol([101.0, -1.0], [99.0, 99.0])

    def test_mismatched_lengths_rejected(self):
        with pytest.raises(ValueError, match="align"):
            parkinson_vol([101.0, 102.0], [99.0])
        with pytest.raises(ValueError, match="equal length"):
            garman_klass_vol([100.0], [101.0, 102.0], [99.0], [100.5])

    def test_high_below_low_rejected(self):
        with pytest.raises(ValueError, match="high"):
            parkinson_vol([98.0], [99.0])

    def test_bad_periods_per_year_rejected(self):
        with pytest.raises(ValueError, match="periods_per_year"):
            parkinson_vol([101.0], [99.0], periods_per_year=0)
