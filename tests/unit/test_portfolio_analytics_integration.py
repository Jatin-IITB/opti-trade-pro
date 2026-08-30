"""Tests for LiveAnalytics with real portfolio integration."""

from __future__ import annotations

import pytest

from options_trading.services.live_analytics import LiveAnalytics, LiveAnalyticsConfig
from optitrade.core.types import (
    OptionContract,
    OptionType,
    Portfolio,
    Position,
)


def _make_portfolio(n_positions: int = 1) -> Portfolio:
    positions = []
    for i in range(n_positions):
        contract = OptionContract(
            symbol="NIFTY",
            strike=24000.0 + i * 500,
            expiry=0.05,
            option_type=OptionType.CALL if i % 2 == 0 else OptionType.PUT,
            lot_size=50,
        )
        positions.append(
            Position(contract=contract, quantity=50.0 if i % 2 == 0 else -50.0, entry_price=180.0)
        )
    return Portfolio(
        positions=tuple(positions),
        equity=5000.0,
        high_water_mark=6000.0,
        margin_available=100000.0,
    )


@pytest.fixture()
def analytics():
    return LiveAnalytics(LiveAnalyticsConfig(vol_model="essvi", essvi_seed=42))


@pytest.fixture()
def raw_chain():
    from optitrade.data.models import RawChain, RawQuote

    quotes = []
    spot = 24500.0
    for offset in range(-5, 6):
        strike = spot + offset * 100
        for ot in [OptionType.CALL, OptionType.PUT]:
            intrinsic = max(0, spot - strike) if ot == OptionType.CALL else max(0, strike - spot)
            mid = intrinsic + 200.0
            quotes.append(
                RawQuote(
                    strike=strike,
                    expiry=0.05,
                    option_type=ot,
                    bid=mid - 5.0,
                    ask=mid + 5.0,
                    ltp=mid,
                    volume=1000,
                    open_interest=5000,
                )
            )
    return RawChain(
        underlying="NIFTY",
        spot=spot,
        rate=0.065,
        timestamp=1_724_000_000.0,
        quotes=tuple(quotes),
    )


class TestGreeksBookWithPortfolio:
    def test_uses_portfolio_positions(self, analytics, raw_chain):
        portfolio = _make_portfolio(2)
        payload = analytics.build_from_raw_chain(raw_chain, portfolio=portfolio)
        book = payload.greeks_book
        assert book is not None
        assert len(book["positions"]) == 2

    def test_positions_have_quantity(self, analytics, raw_chain):
        portfolio = _make_portfolio(1)
        payload = analytics.build_from_raw_chain(raw_chain, portfolio=portfolio)
        book = payload.greeks_book
        assert book["positions"][0]["quantity"] == 50.0

    def test_positions_have_greeks(self, analytics, raw_chain):
        portfolio = _make_portfolio(1)
        payload = analytics.build_from_raw_chain(raw_chain, portfolio=portfolio)
        pos = payload.greeks_book["positions"][0]
        greeks = pos["greeks"]
        assert "delta" in greeks
        assert "gamma" in greeks
        assert "vega" in greeks
        assert "theta" in greeks

    def test_falls_back_without_portfolio(self, analytics, raw_chain):
        payload = analytics.build_from_raw_chain(raw_chain, portfolio=None)
        book = payload.greeks_book
        assert book is not None
        assert len(book["positions"]) > 0


class TestRiskDashboardWithPortfolio:
    def test_computes_real_greeks(self, analytics, raw_chain):
        portfolio = _make_portfolio(2)
        payload = analytics.build_from_raw_chain(raw_chain, portfolio=portfolio)
        risk = payload.risk_dashboard
        assert risk is not None
        current = risk["current"]
        assert current["delta"] != 0.0 or current["gamma"] != 0.0

    def test_shows_drawdown(self, analytics, raw_chain):
        portfolio = _make_portfolio(1)
        payload = analytics.build_from_raw_chain(raw_chain, portfolio=portfolio)
        risk = payload.risk_dashboard
        assert risk["current"]["drawdown"] > 0.0

    def test_zero_without_portfolio(self, analytics, raw_chain):
        payload = analytics.build_from_raw_chain(raw_chain, portfolio=None)
        risk = payload.risk_dashboard
        assert risk["current"]["delta"] == 0.0
        assert risk["current"]["gamma"] == 0.0
        assert risk["current"]["drawdown"] == 0.0

    def test_limits_always_present(self, analytics, raw_chain):
        payload = analytics.build_from_raw_chain(raw_chain, portfolio=_make_portfolio())
        risk = payload.risk_dashboard
        assert "delta" in risk["limits"]
        assert "gamma" in risk["limits"]
        assert "vega" in risk["limits"]


class TestDashboardPayloadCompleteness:
    def test_all_tabs_populated(self, analytics, raw_chain):
        portfolio = _make_portfolio(2)
        payload = analytics.build_from_raw_chain(raw_chain, portfolio=portfolio)
        assert payload.vol_surface is not None
        assert payload.option_chain is not None
        assert payload.greeks_book is not None
        assert payload.essvi_calibration is not None
        assert payload.risk_dashboard is not None

    def test_spot_and_timestamp_set(self, analytics, raw_chain):
        payload = analytics.build_from_raw_chain(raw_chain, portfolio=_make_portfolio())
        assert payload.spot == 24500.0
        assert payload.timestamp == 1_724_000_000.0
        assert payload.underlying == "NIFTY"
