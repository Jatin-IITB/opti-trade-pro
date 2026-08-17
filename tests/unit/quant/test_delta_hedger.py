"""Tests for the band-based DeltaHedger decision logic."""

import pytest

from optitrade.hedging import BandParams, DeltaHedger, ScalpingParams, whalley_wilmott_half_width

pytestmark = pytest.mark.unit

# proportional_cost=0 makes the raw WW width 0, so min_half_width pins the
# band exactly and the tests are deterministic in the half-width.
FIXED_BAND = BandParams(proportional_cost=0.0, risk_aversion=1.0, min_half_width=5.0)


@pytest.fixture
def hedger():
    return DeltaHedger("NIFTY", FIXED_BAND)


class TestHoldInsideBand:
    def test_inside_band_holds(self, hedger):
        decision = hedger.decide(portfolio_delta=3.0, gamma=0.05, spot=100.0)
        assert decision.action == "hold"
        assert decision.order is None
        assert decision.band_half_width == 5.0
        assert decision.band_scale == 1.0
        assert decision.confidence == 0.0
        assert "3.0000" in decision.rationale
        assert "5.0000" in decision.rationale

    def test_exactly_on_the_edge_holds(self, hedger):
        assert hedger.decide(portfolio_delta=5.0, gamma=0.05, spot=100.0).action == "hold"


class TestRebalanceOutsideBand:
    def test_long_delta_sells_rounded_toward_zero(self, hedger):
        decision = hedger.decide(portfolio_delta=25.7, gamma=0.05, spot=100.0)
        assert decision.action == "rebalance"
        assert decision.order is not None
        assert decision.order.quantity == -25.0  # trunc(-25.7) -> -25
        assert decision.order.symbol == "NIFTY"
        assert decision.order.price == 100.0
        assert decision.order.contract is None
        assert decision.portfolio_delta == 25.7

    def test_short_delta_buys(self, hedger):
        decision = hedger.decide(portfolio_delta=-25.7, gamma=0.05, spot=100.0)
        assert decision.order is not None
        assert decision.order.quantity == 25.0

    def test_rationale_contains_the_numbers(self, hedger):
        decision = hedger.decide(portfolio_delta=25.7, gamma=0.05, spot=101.5)
        assert "25.7000" in decision.rationale  # portfolio delta
        assert "5.0000" in decision.rationale  # band half-width
        assert "25" in decision.rationale  # shares traded
        assert "101.50" in decision.rationale  # spot
        assert "NIFTY" in decision.rationale

    def test_sub_share_breach_rounds_to_zero_and_holds(self):
        hedger = DeltaHedger(
            "NIFTY", BandParams(proportional_cost=0.0, risk_aversion=1.0, min_half_width=0.5)
        )
        decision = hedger.decide(portfolio_delta=0.7, gamma=0.05, spot=100.0)
        assert decision.action == "hold"
        assert decision.order is None
        assert "zero whole shares" in decision.rationale


class TestConfidence:
    def test_far_outside_band_is_capped_at_one(self, hedger):
        # |delta| = 20 is 3 half-widths beyond the 5.0 band -> capped.
        assert hedger.decide(20.0, 0.05, 100.0).confidence == 1.0

    def test_just_outside_band_is_proportional(self, hedger):
        # (6 - 5) / 5 = 0.2 half-widths outside.
        assert hedger.decide(6.0, 0.05, 100.0).confidence == pytest.approx(0.2)

    def test_confidence_always_in_unit_interval(self, hedger):
        for delta in (-40.0, -6.0, -1.0, 0.0, 0.3, 4.99, 5.01, 12.0, 1e6):
            assert 0.0 <= hedger.decide(delta, 0.05, 100.0).confidence <= 1.0


class TestScalpingScale:
    WW_BAND = BandParams(proportional_cost=0.002, risk_aversion=1.0)

    def test_band_tightens_when_rv_above_iv(self):
        hedger = DeltaHedger("NIFTY", self.WW_BAND, ScalpingParams(band_params=self.WW_BAND))
        raw = whalley_wilmott_half_width(0.05, 100.0, self.WW_BAND)
        decision = hedger.decide(1.0, 0.05, 100.0, realized_vol=0.30, implied_vol=0.20)
        assert decision.band_scale == 0.5
        assert decision.band_half_width == pytest.approx(0.5 * raw)

    def test_band_widens_when_rv_below_iv(self):
        hedger = DeltaHedger("NIFTY", self.WW_BAND, ScalpingParams(band_params=self.WW_BAND))
        raw = whalley_wilmott_half_width(0.05, 100.0, self.WW_BAND)
        decision = hedger.decide(1.0, 0.05, 100.0, realized_vol=0.10, implied_vol=0.20)
        assert decision.band_scale == 2.0
        assert decision.band_half_width == pytest.approx(2.0 * raw)

    def test_no_scaling_without_vols_or_params(self):
        with_params = DeltaHedger("NIFTY", self.WW_BAND, ScalpingParams(band_params=self.WW_BAND))
        without_params = DeltaHedger("NIFTY", self.WW_BAND)
        assert with_params.decide(1.0, 0.05, 100.0).band_scale == 1.0
        assert with_params.decide(1.0, 0.05, 100.0, realized_vol=0.3).band_scale == 1.0
        assert (
            without_params.decide(1.0, 0.05, 100.0, realized_vol=0.3, implied_vol=0.2).band_scale
            == 1.0
        )
