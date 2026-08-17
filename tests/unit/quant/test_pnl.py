"""Tests for Taylor P&L attribution and the theta-tracking metric."""

import numpy as np
import pytest

from optitrade.core import NumericalError
from optitrade.hedging import attribute_pnl, theta_tracking_error
from optitrade.pricing import bs_greeks_at, bs_price

pytestmark = pytest.mark.unit


class TestAttributePnl:
    def test_pure_bs_move_has_small_residual(self):
        # Reprice an ATM call after a small (dS, dt) move at realized == implied;
        # second-order Taylor should explain almost all of the P&L.
        spot, strike, expiry, rate, vol = 100.0, 100.0, 0.5, 0.02, 0.2
        d_spot, d_time = 0.5, 1.0 / 252.0
        greeks = bs_greeks_at(spot, strike, expiry, rate, vol)
        total = float(bs_price(spot + d_spot, strike, expiry - d_time, rate, vol)) - float(
            bs_price(spot, strike, expiry, rate, vol)
        )

        attribution = attribute_pnl(
            greeks, d_spot=d_spot, d_vol=0.0, d_time=d_time, total_pnl=total
        )

        # Measured residual is ~0.06% of total; assert with generous headroom.
        assert abs(attribution.residual) < 0.02 * abs(total)
        assert attribution.total == total
        assert attribution.delta_pnl == pytest.approx(greeks.delta * d_spot)
        assert attribution.gamma_pnl == pytest.approx(0.5 * greeks.gamma * d_spot**2)
        assert attribution.theta_pnl == pytest.approx(greeks.theta * d_time)
        assert attribution.vega_pnl == 0.0

    def test_buckets_and_residual_sum_to_total(self):
        greeks = bs_greeks_at(100.0, 105.0, 0.25, 0.03, 0.25)
        attribution = attribute_pnl(
            greeks, d_spot=-1.2, d_vol=0.01, d_time=2.0 / 252.0, total_pnl=0.4
        )
        explained = (
            attribution.delta_pnl
            + attribution.gamma_pnl
            + attribution.theta_pnl
            + attribution.vega_pnl
        )
        assert explained + attribution.residual == pytest.approx(attribution.total)

    def test_vega_bucket_captures_vol_move(self):
        greeks = bs_greeks_at(100.0, 100.0, 0.5, 0.02, 0.2)
        attribution = attribute_pnl(greeks, d_spot=0.0, d_vol=0.02, d_time=0.0, total_pnl=0.0)
        assert attribution.vega_pnl == pytest.approx(greeks.vega * 0.02)


class TestThetaTrackingError:
    def test_hand_checked_value(self):
        # |sum(hedged) - sum(theta)| / |sum(theta)| = |6 - 5| / 5 = 0.2
        hedged = np.array([1.0, 2.0, 3.0])
        theta = np.array([2.0, 2.0, 1.0])
        assert theta_tracking_error(hedged, theta) == pytest.approx(0.2)

    def test_sign_of_theta_total_is_irrelevant(self):
        hedged = np.array([-6.0])
        theta = np.array([-5.0])
        assert theta_tracking_error(hedged, theta) == pytest.approx(0.2)

    def test_perfect_tracking_is_zero(self):
        pnl = np.array([-0.5, -0.3, -0.2])
        assert theta_tracking_error(pnl, pnl.copy()) == 0.0

    def test_zero_theta_total_raises(self):
        with pytest.raises(NumericalError, match="theta"):
            theta_tracking_error(np.array([1.0, 2.0]), np.array([1e-14, -1e-14]))
