"""Tests for the live dashboard analytics payload builder."""

import pytest

from options_trading.services.live_analytics import (
    LiveAnalytics,
    LiveAnalyticsConfig,
    LiveDashboardPayload,
)
from optitrade.data.capture import SyntheticSource


@pytest.fixture()
def chain():
    return SyntheticSource(seed=42).fetch_chain("NIFTY")


@pytest.fixture()
def payload(chain) -> LiveDashboardPayload:
    analytics = LiveAnalytics(LiveAnalyticsConfig(vol_model="essvi"))
    return analytics.build_from_raw_chain(chain)


class TestBuildFromRawChain:
    def test_returns_payload(self, payload):
        assert isinstance(payload, LiveDashboardPayload)

    def test_spot_and_underlying(self, payload, chain):
        assert payload.spot == chain.spot
        assert payload.underlying == chain.underlying
        assert payload.timestamp == chain.timestamp


class TestVolSurface:
    def test_shape(self, payload):
        vs = payload.vol_surface
        assert vs is not None
        assert "strikes" in vs
        assert "expiries" in vs
        assert "ivs" in vs
        assert "spot" in vs

    def test_ivs_is_2d(self, payload):
        vs = payload.vol_surface
        assert isinstance(vs["ivs"], list)
        assert len(vs["ivs"]) == len(vs["expiries"])
        for row in vs["ivs"]:
            assert isinstance(row, list)
            assert len(row) == len(vs["strikes"])

    def test_ivs_are_positive(self, payload):
        for row in payload.vol_surface["ivs"]:
            for v in row:
                assert v > 0, f"IV must be positive, got {v}"

    def test_spot_matches(self, payload, chain):
        assert payload.vol_surface["spot"] == chain.spot


class TestOptionChain:
    def test_shape(self, payload):
        oc = payload.option_chain
        assert oc is not None
        assert "spot" in oc
        assert "expiry" in oc
        assert "chain" in oc

    def test_chain_rows_have_all_fields(self, payload):
        for row in payload.option_chain["chain"]:
            for field in (
                "strike",
                "callPrice",
                "putPrice",
                "callDelta",
                "putDelta",
                "gamma",
                "vega",
                "iv",
                "oi",
            ):
                assert field in row, f"missing field {field}"

    def test_chain_rows_nonempty(self, payload):
        assert len(payload.option_chain["chain"]) > 0

    def test_strikes_are_sorted(self, payload):
        strikes = [r["strike"] for r in payload.option_chain["chain"]]
        assert strikes == sorted(strikes)


class TestGreeksBook:
    def test_shape(self, payload):
        gb = payload.greeks_book
        assert gb is not None
        assert "spot" in gb
        assert "rate" in gb
        assert "positions" in gb

    def test_positions_have_greeks(self, payload):
        for pos in payload.greeks_book["positions"]:
            assert "greeks" in pos
            greeks = pos["greeks"]
            for field in ("delta", "gamma", "vega", "theta", "rho", "vanna", "volga"):
                assert field in greeks

    def test_positions_have_metadata(self, payload):
        for pos in payload.greeks_book["positions"]:
            for field in ("strike", "expiry", "vol", "optionType", "price"):
                assert field in pos


class TestEssviCalibration:
    def test_shape(self, payload):
        ec = payload.essvi_calibration
        assert ec is not None
        assert "expiries" in ec
        assert "spot" in ec
        assert "params" in ec
        assert "durrlemanViolations" in ec

    def test_params_present(self, payload):
        params = payload.essvi_calibration["params"]
        assert "rho" in params
        assert "eta" in params
        assert "gamma" in params

    def test_expiry_slices_have_data(self, payload):
        for sl in payload.essvi_calibration["expiries"]:
            assert "t" in sl
            assert "strikes" in sl
            assert "marketVols" in sl
            assert "fittedVols" in sl
            assert len(sl["strikes"]) > 0
            assert len(sl["marketVols"]) == len(sl["strikes"])


class TestRiskDashboard:
    def test_shape(self, payload):
        rd = payload.risk_dashboard
        assert rd is not None
        assert "limits" in rd
        assert "current" in rd
        assert "utilizationHistory" in rd
        assert "verdicts" in rd

    def test_limits_keys(self, payload):
        for key in ("delta", "gamma", "vega", "drawdown"):
            assert key in payload.risk_dashboard["limits"]


class TestPartialFailure:
    def test_one_builder_failure_does_not_block_others(self, chain):
        config = LiveAnalyticsConfig(vol_model="essvi", n_strike_grid=0)
        analytics = LiveAnalytics(config)
        payload = analytics.build_from_raw_chain(chain)
        assert payload.spot > 0
        non_none = sum(
            1
            for attr in (
                payload.vol_surface,
                payload.option_chain,
                payload.greeks_book,
                payload.essvi_calibration,
                payload.risk_dashboard,
            )
            if attr is not None
        )
        assert non_none >= 1


class TestDeterminism:
    def test_same_seed_same_result(self, chain):
        a = LiveAnalytics().build_from_raw_chain(chain)
        b = LiveAnalytics().build_from_raw_chain(chain)
        assert a.vol_surface["ivs"] == b.vol_surface["ivs"]
        assert len(a.option_chain["chain"]) == len(b.option_chain["chain"])
