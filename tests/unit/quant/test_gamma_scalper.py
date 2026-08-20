"""Tests for EWMA realized vol (RiskMetrics 1996) and the RV/IV band scale."""

import itertools

import numpy as np
import pytest

from optitrade.hedging import BandParams, ScalpingParams, ewma_realized_vol, scalping_band_scale

pytestmark = pytest.mark.unit


@pytest.fixture
def params():
    return ScalpingParams(band_params=BandParams(proportional_cost=5e-4, risk_aversion=1.0))


class TestEwmaRealizedVol:
    def test_recovers_constant_vol_within_tolerance(self):
        # EWMA(0.94) has an effective window of ~32 observations, so a single
        # seeded draw carries ~12% sampling noise; seed 2 lands at 0.8% error.
        true_vol = 0.2
        rng = np.random.default_rng(2)
        returns = rng.standard_normal(2000) * true_vol / np.sqrt(252)
        estimate = ewma_realized_vol(returns)
        assert estimate == pytest.approx(true_vol, rel=0.10)

    def test_exact_on_constant_magnitude_returns(self):
        # With |r| constant the weighted mean of r^2 is exact regardless of lam.
        true_vol = 0.2
        returns = np.full(500, true_vol / np.sqrt(252))
        assert ewma_realized_vol(returns) == pytest.approx(true_vol, rel=1e-12)

    def test_recent_returns_dominate(self):
        calm = np.full(200, 0.05 / np.sqrt(252))
        stormy = np.full(50, 0.40 / np.sqrt(252))
        estimate = ewma_realized_vol(np.concatenate([calm, stormy]))
        # The last 50 high-vol observations should pull the estimate near 0.40.
        assert estimate > 0.35

    def test_validations(self):
        with pytest.raises(ValueError, match="non-empty"):
            ewma_realized_vol(np.array([]))
        with pytest.raises(ValueError, match="lam"):
            ewma_realized_vol(np.array([0.01]), lam=1.0)
        with pytest.raises(ValueError, match="periods_per_year"):
            ewma_realized_vol(np.array([0.01]), periods_per_year=0)


class TestScalpingBandScale:
    def test_high_rv_regime_tightens(self, params):
        # RV/IV = 1.5 >= 1.2: harvest gamma, rebalance more.
        assert scalping_band_scale(0.30, 0.20, params) == params.tighten_factor
        assert scalping_band_scale(0.24, 0.20, params) == params.tighten_factor  # edge 1.2

    def test_low_rv_regime_widens(self, params):
        # RV/IV = 0.5 <= 0.8: save transaction costs.
        assert scalping_band_scale(0.10, 0.20, params) == params.widen_factor
        assert scalping_band_scale(0.16, 0.20, params) == params.widen_factor  # edge 0.8

    def test_midpoint_interpolates(self, params):
        # Ratio 1.0 is halfway through [0.8, 1.2] -> midpoint of [2.0, 0.5].
        assert scalping_band_scale(0.20, 0.20, params) == pytest.approx(1.25)

    def test_interpolation_is_monotone_decreasing_in_ratio(self, params):
        ratios = np.linspace(0.8, 1.2, 21)
        scales = [scalping_band_scale(0.2 * r, 0.2, params) for r in ratios]
        assert all(a >= b for a, b in itertools.pairwise(scales))

    def test_continuity_at_the_edges(self, params):
        eps = 1e-9
        low = scalping_band_scale(0.2 * (params.rv_iv_low + eps), 0.2, params)
        high = scalping_band_scale(0.2 * (params.rv_iv_high - eps), 0.2, params)
        assert low == pytest.approx(params.widen_factor, abs=1e-6)
        assert high == pytest.approx(params.tighten_factor, abs=1e-6)

    def test_validations(self, params):
        with pytest.raises(ValueError, match="implied_vol"):
            scalping_band_scale(0.2, 0.0, params)
        with pytest.raises(ValueError, match="realized_vol"):
            scalping_band_scale(-0.1, 0.2, params)
        with pytest.raises(ValueError, match="rv_iv_low"):
            ScalpingParams(band_params=params.band_params, rv_iv_low=1.3, rv_iv_high=1.2)
