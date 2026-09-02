"""Tests for the live dashboard analytics payload builder."""

import pytest

from options_trading.config.settings import settings
from options_trading.services.live_analytics import (
    BookContext,
    LiveAnalytics,
    LiveAnalyticsConfig,
    LiveDashboardPayload,
)
from optitrade.core.types import OptionContract, Portfolio, Position
from optitrade.data.capture import SyntheticSource, to_market_snapshot


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


class TestRiskDashboardShape:
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


def _book_from_chain(chain, n_legs: int = 2) -> BookContext:
    """Build a BookContext from the nearest-expiry ATM strikes of a chain.

    Uses real quotes so the marks are invertible, which is what the book
    panels require.
    """
    snapshot = to_market_snapshot(chain)
    near = min(q.expiry for q in snapshot.quotes)
    atm = sorted(
        (q for q in snapshot.quotes if q.expiry == near),
        key=lambda q: abs(q.strike - snapshot.spot),
    )[:n_legs]

    positions = []
    marks = {}
    for i, q in enumerate(atm):
        symbol = f"LEG{i}"
        positions.append(
            Position(
                contract=OptionContract(
                    symbol=symbol,
                    strike=q.strike,
                    expiry=q.expiry,
                    option_type=q.option_type,
                    lot_size=50,
                ),
                quantity=50.0,
                entry_price=q.mid,
            )
        )
        marks[symbol] = q.mid

    return BookContext(
        portfolio=Portfolio(positions=tuple(positions), equity=200_000.0),
        marks=marks,
        equity=200_000.0,
        margin_used=45_000.0,
        margin_available=155_000.0,
    )


class TestScenarioGrid:
    def test_absent_without_a_book(self, payload):
        """A grid for a contract the user does not hold answers nothing."""
        assert payload.scenario_grid is None

    def test_built_from_the_real_book(self, chain):
        analytics = LiveAnalytics(LiveAnalyticsConfig(vol_model="essvi"))
        result = analytics.build_from_raw_chain(chain, _book_from_chain(chain))

        grid = result.scenario_grid
        assert grid is not None
        assert grid["legsPriced"] == 2
        assert grid["legsExcluded"] == 0

    def test_pnl_is_vol_by_spot_for_plotly(self, chain):
        """Plotly heatmap z is indexed [y][x]; y is vol, x is spot.

        Regression guard: the engine cube is (spot, vol, time), so emitting it
        untransposed silently draws the heatmap sideways.
        """
        analytics = LiveAnalytics(LiveAnalyticsConfig(vol_model="essvi"))
        grid = analytics.build_from_raw_chain(chain, _book_from_chain(chain)).scenario_grid

        assert len(grid["pnl"]) == len(grid["volShifts"])
        assert len(grid["pnl"][0]) == len(grid["spotShifts"])

    def test_axes_are_percent_not_fractions(self, chain):
        """The heatmap axes render with a '%' suffix.

        Emitting 0.10 for a 10% move would draw it as 0.1%.
        """
        analytics = LiveAnalytics(LiveAnalyticsConfig(vol_model="essvi"))
        grid = analytics.build_from_raw_chain(chain, _book_from_chain(chain)).scenario_grid

        assert max(grid["spotShifts"]) == pytest.approx(10.0)
        assert min(grid["spotShifts"]) == pytest.approx(-10.0)
        assert max(grid["volShifts"]) == pytest.approx(5.0)

    def test_base_scenario_has_zero_pnl(self, chain):
        analytics = LiveAnalytics(LiveAnalyticsConfig(vol_model="essvi"))
        grid = analytics.build_from_raw_chain(chain, _book_from_chain(chain)).scenario_grid

        spot_mid = grid["spotShifts"].index(0.0)
        vol_mid = grid["volShifts"].index(0.0)
        assert grid["pnl"][vol_mid][spot_mid] == pytest.approx(0.0, abs=1e-6)

    def test_worst_is_the_grid_minimum(self, chain):
        analytics = LiveAnalytics(LiveAnalyticsConfig(vol_model="essvi"))
        grid = analytics.build_from_raw_chain(chain, _book_from_chain(chain)).scenario_grid

        flat = [v for row in grid["pnl"] for v in row]
        assert grid["worst"]["pnl"] == pytest.approx(min(flat))
        assert grid["best"]["pnl"] == pytest.approx(max(flat))


class TestRiskDashboard:
    def test_reports_no_book_rather_than_zeros(self, payload):
        """Zero exposure reads as a flat book; absent must be distinguishable."""
        risk = payload.risk_dashboard
        assert risk["hasBook"] is False
        assert risk["current"] is None
        assert risk["verdicts"] is None
        assert risk["drawdown"] is None

    def test_limits_come_from_config(self, payload):
        limits = payload.risk_dashboard["limits"]
        assert limits["delta"] == settings.risk_max_abs_delta
        assert limits["vega"] == settings.risk_max_abs_vega

    def test_exposure_from_the_real_book(self, chain):
        analytics = LiveAnalytics(LiveAnalyticsConfig(vol_model="essvi"))
        risk = analytics.build_from_raw_chain(chain, _book_from_chain(chain)).risk_dashboard

        assert risk["hasBook"] is True
        assert risk["legsPriced"] == 2
        assert risk["current"]["vega"] > 0  # long two ATM options
        assert risk["current"]["gamma"] > 0

    def test_margin_utilization_from_funds(self, chain):
        analytics = LiveAnalytics(LiveAnalyticsConfig(vol_model="essvi"))
        risk = analytics.build_from_raw_chain(chain, _book_from_chain(chain)).risk_dashboard

        # 45k used of 200k equity
        assert risk["marginUtilization"] == pytest.approx(0.225)


class TestHigherOrderGreeks:
    def test_built_from_the_live_atm_contract(self, payload):
        hog = payload.higher_order_greeks
        assert hog is not None
        for key in ("charm", "veta", "speed", "color", "ultima", "zomma"):
            assert key in hog
            assert isinstance(hog[key], float)

    def test_reports_the_contract_it_priced(self, payload, chain):
        contract = payload.higher_order_greeks["contract"]
        assert contract["spot"] == pytest.approx(chain.spot)
        assert contract["vol"] > 0
        assert contract["optionType"] in ("call", "put")
