"""Tests for the Whalley-Wilmott (1997) no-transaction band."""

import numpy as np
import pytest

from optitrade.hedging import BandParams, whalley_wilmott_half_width

pytestmark = pytest.mark.unit


class TestWhalleyWilmottFormula:
    def test_hand_checked_value(self):
        # H = (1.5 * k * S * Gamma^2 / lambda)^(1/3)
        #   = (1.5 * 0.002 * 100 * 0.05^2 / 1.0)^(1/3) = 0.00075^(1/3)
        params = BandParams(proportional_cost=0.002, risk_aversion=1.0)
        h = whalley_wilmott_half_width(gamma=0.05, spot=100.0, params=params)
        assert h == pytest.approx(0.00075 ** (1.0 / 3.0), rel=1e-12)
        assert h == pytest.approx(0.090856029641607, rel=1e-9)

    def test_gamma_scaling_exponent(self):
        # H scales as |Gamma|^(2/3): quadrupling gamma multiplies H by 4^(2/3).
        params = BandParams(proportional_cost=5e-4, risk_aversion=2.0)
        h_small = whalley_wilmott_half_width(0.05, 100.0, params)
        h_large = whalley_wilmott_half_width(0.20, 100.0, params)
        assert h_large / h_small == pytest.approx(4.0 ** (2.0 / 3.0), rel=1e-9)

    def test_sign_of_gamma_is_irrelevant(self):
        params = BandParams(proportional_cost=5e-4, risk_aversion=1.0)
        assert whalley_wilmott_half_width(-0.1, 100.0, params) == pytest.approx(
            whalley_wilmott_half_width(0.1, 100.0, params)
        )


class TestMonotonicity:
    def test_wider_band_for_higher_abs_gamma(self):
        params = BandParams(proportional_cost=5e-4, risk_aversion=1.0)
        widths = [whalley_wilmott_half_width(g, 100.0, params) for g in (0.01, 0.05, 0.2)]
        assert widths[0] < widths[1] < widths[2]

    def test_narrower_band_for_higher_risk_aversion(self):
        low = BandParams(proportional_cost=5e-4, risk_aversion=0.5)
        high = BandParams(proportional_cost=5e-4, risk_aversion=5.0)
        assert whalley_wilmott_half_width(0.05, 100.0, high) < whalley_wilmott_half_width(
            0.05, 100.0, low
        )

    def test_wider_band_for_higher_cost(self):
        cheap = BandParams(proportional_cost=1e-4, risk_aversion=1.0)
        dear = BandParams(proportional_cost=1e-3, risk_aversion=1.0)
        assert whalley_wilmott_half_width(0.05, 100.0, cheap) < whalley_wilmott_half_width(
            0.05, 100.0, dear
        )


class TestClampingAndEdges:
    def test_clamped_to_min_half_width(self):
        params = BandParams(proportional_cost=1e-8, risk_aversion=100.0, min_half_width=0.5)
        assert whalley_wilmott_half_width(1e-4, 100.0, params) == 0.5

    def test_clamped_to_max_half_width(self):
        params = BandParams(proportional_cost=0.05, risk_aversion=1e-6, max_half_width=0.01)
        assert whalley_wilmott_half_width(5.0, 100.0, params) == 0.01

    def test_zero_gamma_returns_min_half_width(self):
        params = BandParams(proportional_cost=5e-4, risk_aversion=1.0, min_half_width=0.02)
        assert whalley_wilmott_half_width(0.0, 100.0, params) == 0.02
        default = BandParams(proportional_cost=5e-4, risk_aversion=1.0)
        assert whalley_wilmott_half_width(0.0, 100.0, default) == 0.0

    def test_unclamped_by_default(self):
        params = BandParams(proportional_cost=5e-4, risk_aversion=1.0)
        assert params.max_half_width == np.inf
        assert whalley_wilmott_half_width(0.05, 100.0, params) > 0.0


class TestValidation:
    def test_non_positive_risk_aversion_rejected(self):
        with pytest.raises(ValueError, match="risk_aversion"):
            BandParams(proportional_cost=5e-4, risk_aversion=0.0)

    def test_negative_cost_rejected(self):
        with pytest.raises(ValueError, match="proportional_cost"):
            BandParams(proportional_cost=-1e-4, risk_aversion=1.0)

    def test_max_below_min_rejected(self):
        with pytest.raises(ValueError, match="max_half_width"):
            BandParams(
                proportional_cost=5e-4, risk_aversion=1.0, min_half_width=1.0, max_half_width=0.5
            )

    def test_non_positive_spot_rejected(self):
        params = BandParams(proportional_cost=5e-4, risk_aversion=1.0)
        with pytest.raises(ValueError, match="spot"):
            whalley_wilmott_half_width(0.05, 0.0, params)
