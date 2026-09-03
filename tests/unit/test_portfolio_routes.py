"""Tests for portfolio REST API routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from options_trading.api.routes.portfolio import router as portfolio_router
from options_trading.services.portfolio_client import (
    UpstoxFunds,
    UpstoxHolding,
    UpstoxOrder,
    UpstoxPosition,
)
from options_trading.services.portfolio_sync_service import PortfolioSyncStatus
from options_trading.services.token_provider import TokenProvider
from optitrade.core.types import (
    OptionContract,
    OptionType,
    Portfolio,
    Position,
)


def _sample_position() -> UpstoxPosition:
    return UpstoxPosition(
        instrument_key="NSE_FO|NIFTY2490724500CE",
        trading_symbol="NIFTY2490724500CE",
        exchange="NSE_FO",
        product="D",
        quantity=50,
        buy_price=180.0,
        sell_price=0.0,
        last_price=260.0,
        pnl=4000.0,
        multiplier=50,
        option_type="CE",
        strike_price=24500.0,
        expiry="2025-09-07",
    )


def _sample_holding() -> UpstoxHolding:
    return UpstoxHolding(
        instrument_key="INE002A01018",
        trading_symbol="RELIANCE",
        exchange="NSE",
        quantity=10,
        average_price=2450.0,
        last_price=2520.0,
        pnl=700.0,
        day_change=15.5,
        day_change_percentage=0.62,
    )


def _sample_order() -> UpstoxOrder:
    return UpstoxOrder(
        order_id="240830000001",
        trading_symbol="NIFTY2490724500CE",
        exchange="NSE_FO",
        order_type="LIMIT",
        transaction_type="BUY",
        quantity=50,
        price=180.0,
        trigger_price=0.0,
        status="complete",
        filled_quantity=50,
        average_price=180.0,
        placed_at="2025-08-30T10:15:00",
        product="D",
    )


def _sample_portfolio() -> Portfolio:
    # symbol must equal the UpstoxPosition.trading_symbol: to_core_portfolio
    # sets it that way, and the summary route keys current marks off it.
    contract = OptionContract(
        symbol="NIFTY2490724500CE",
        strike=24500.0,
        expiry=0.05,
        option_type=OptionType.CALL,
        lot_size=50,
    )
    return Portfolio(
        positions=(Position(contract=contract, quantity=50.0, entry_price=180.0),),
        equity=200_000.0,
        margin_available=155_000.0,
    )


SAMPLE_SPOT = 24_600.0


def _mock_sync_service(
    positions: list | None = None,
    holdings: list | None = None,
    orders: list | None = None,
    portfolio: Portfolio | None = None,
    spot: float | None = SAMPLE_SPOT,
    funds: UpstoxFunds | None = None,
) -> MagicMock:
    svc = MagicMock()
    svc.get_latest_positions.return_value = (
        positions if positions is not None else [_sample_position()]
    )
    svc.get_latest_holdings.return_value = holdings if holdings is not None else [_sample_holding()]
    svc.get_latest_orders.return_value = orders if orders is not None else [_sample_order()]
    svc.get_latest_portfolio.return_value = (
        portfolio if portfolio is not None else _sample_portfolio()
    )
    svc.get_latest_spot.return_value = spot
    svc.get_latest_funds.return_value = (
        funds
        if funds is not None
        else UpstoxFunds(used_margin=45_000.0, available_margin=155_000.0)
    )
    svc.status.return_value = PortfolioSyncStatus(
        running=True,
        last_sync_ts=1_724_000_000.0,
        n_syncs=5,
        n_failures=0,
        position_count=1,
        spot=spot,
    )
    return svc


def _no_auth_user():
    return {"user_id": "test-user", "token": "test-token"}


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(portfolio_router, prefix="/api/v1")
    sync_svc = _mock_sync_service()
    app.state.portfolio_sync = sync_svc
    app.dependency_overrides[
        __import__(
            "options_trading.utils.auth_dependencies", fromlist=["get_current_user"]
        ).get_current_user
    ] = _no_auth_user
    return TestClient(app)


@pytest.fixture()
def client_no_sync():
    app = FastAPI()
    app.include_router(portfolio_router, prefix="/api/v1")
    app.state.portfolio_sync = None
    app.dependency_overrides[
        __import__(
            "options_trading.utils.auth_dependencies", fromlist=["get_current_user"]
        ).get_current_user
    ] = _no_auth_user
    # A provider that cannot resolve, injected rather than patched.
    #
    # `TokenProvider.__init__` takes `auth_service_factory=AuthService` as a
    # *default argument*, so the real class is captured into __defaults__ at
    # definition time and no module-attribute patch can reach it. The previous
    # `patch("...auth_service.AuthService")` was therefore inert: the route's
    # lazy-init path called the real AuthService, and this test's verdict came
    # down to whether the machine happened to hold a resolvable token. It
    # passed on CI, passed here by test ordering, and failed as soon as the
    # ordering changed — exactly the machine-state dependence CLAUDE.md forbids.
    mock_auth = MagicMock()
    mock_auth.__aenter__ = AsyncMock(side_effect=RuntimeError("no token"))
    mock_auth.__aexit__ = AsyncMock(return_value=False)
    app.state.token_providers = {"default": TokenProvider(auth_service_factory=lambda: mock_auth)}
    yield TestClient(app)


class TestPortfolioPositions:
    def test_returns_positions(self, client):
        resp = client.get("/api/v1/portfolio/positions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["trading_symbol"] == "NIFTY2490724500CE"
        assert data[0]["quantity"] == 50

    def test_503_when_no_sync(self, client_no_sync):
        resp = client_no_sync.get("/api/v1/portfolio/positions")
        assert resp.status_code == 503


class TestPortfolioHoldings:
    def test_returns_holdings(self, client):
        resp = client.get("/api/v1/portfolio/holdings")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["trading_symbol"] == "RELIANCE"


class TestPortfolioOrders:
    def test_returns_orders(self, client):
        resp = client.get("/api/v1/portfolio/orders")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["order_id"] == "240830000001"


class TestPortfolioSummary:
    def test_returns_summary_with_portfolio(self, client):
        resp = client.get("/api/v1/portfolio/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["synced"] is True
        assert data["total_positions"] == 1
        assert data["total_pnl"] == 4000.0

    def test_returns_unsynced_when_no_portfolio(self):
        app = FastAPI()
        app.include_router(portfolio_router, prefix="/api/v1")
        svc = _mock_sync_service(portfolio=None)
        svc.get_latest_portfolio.return_value = None
        app.state.portfolio_sync = svc
        app.dependency_overrides[
            __import__(
                "options_trading.utils.auth_dependencies", fromlist=["get_current_user"]
            ).get_current_user
        ] = _no_auth_user
        c = TestClient(app)
        resp = c.get("/api/v1/portfolio/summary")
        assert resp.status_code == 200
        assert resp.json()["synced"] is False


def _client_with(svc) -> TestClient:
    app = FastAPI()
    app.include_router(portfolio_router, prefix="/api/v1")
    app.state.portfolio_sync = svc
    app.dependency_overrides[
        __import__(
            "options_trading.utils.auth_dependencies", fromlist=["get_current_user"]
        ).get_current_user
    ] = _no_auth_user
    return TestClient(app)


class TestSpotDrivenAnalytics:
    """Guards for the equity/P&L/spot conflation.

    Portfolio.equity was previously set to sum(position.pnl) and then passed
    as ``spot`` into the Greeks and moneyness code. Every Greek was priced at
    a spot of a few thousand rupees for a 24500-strike option, and every CE
    was labelled OTM. These tests pin the corrected behaviour.
    """

    def test_summary_equity_is_funds_not_pnl(self, client):
        resp = client.get("/api/v1/portfolio/summary")
        data = resp.json()
        assert data["equity"] == 200_000.0  # used 45k + available 155k
        assert data["total_pnl"] == 4000.0
        assert data["equity"] != data["total_pnl"]
        assert data["margin_used"] == 45_000.0
        assert data["margin_available"] == 155_000.0
        assert data["margin_utilization"] == pytest.approx(0.225)

    def test_summary_reports_live_spot(self, client):
        assert client.get("/api/v1/portfolio/summary").json()["spot"] == SAMPLE_SPOT

    def test_greeks_omitted_when_no_spot(self):
        """No live spot must yield absent Greeks, never zeroed Greeks.

        A zero delta reads as a flat book; that is a materially different and
        more dangerous claim than "not yet known" (ADR-008, fail closed).
        """
        c = _client_with(_mock_sync_service(spot=None))
        data = c.get("/api/v1/portfolio/summary").json()
        assert data["synced"] is True
        assert data["aggregate_greeks"] is None
        assert data["greeks_priced"] == 0
        assert data["spot"] is None

    def test_greeks_priced_at_live_spot(self, client):
        data = client.get("/api/v1/portfolio/summary").json()
        greeks = data["aggregate_greeks"]
        assert greeks is not None
        assert data["greeks_priced"] == 1
        # 24500 CE with spot at 24600 is near-the-money and long 50 lots:
        # delta must be positive and well inside (0, 50].
        assert 0.0 < greeks["delta"] <= 50.0
        assert greeks["gamma"] > 0.0
        assert greeks["vega"] > 0.0

    def test_moneyness_uses_spot_not_equity(self):
        """A 24500 CE with spot at 26000 is ITM.

        Under the old code spot was total P&L (4000), giving a strike/spot
        ratio of ~6 and labelling every call OTM.
        """
        c = _client_with(_mock_sync_service(spot=26_000.0))
        signals = c.get("/api/v1/portfolio/signals").json()
        assert signals[0]["moneyness"] == "ITM"

    def test_moneyness_unknown_without_spot(self):
        c = _client_with(_mock_sync_service(spot=None))
        signals = c.get("/api/v1/portfolio/signals").json()
        assert signals[0]["moneyness"] == "unknown"

    def test_summary_without_funds_reports_null_equity(self):
        svc = _mock_sync_service()
        svc.get_latest_funds.return_value = None
        c = _client_with(svc)
        data = c.get("/api/v1/portfolio/summary").json()
        assert data["equity"] is None
        assert data["margin_used"] is None
        assert data["total_pnl"] == 4000.0


class TestSyncStatus:
    def test_returns_status(self, client):
        resp = client.get("/api/v1/portfolio/sync/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["n_syncs"] == 5
        assert data["running"] is True


class TestPositionSignals:
    def test_returns_signals(self, client):
        resp = client.get("/api/v1/portfolio/signals")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        signal = data[0]
        assert signal["trading_symbol"] == "NIFTY2490724500CE"
        assert signal["entry_price"] == 180.0
        assert signal["current_price"] == 260.0
        assert signal["pnl"] == 4000.0
        assert signal["days_to_expiry"] is not None


class TestSyncStatusAuthState:
    """The REST contract the frontend re-login banner depends on."""

    def test_exposes_auth_required(self):
        svc = _mock_sync_service()
        svc.status.return_value = PortfolioSyncStatus(
            running=True,
            last_sync_ts=1_724_000_000.0,
            n_syncs=5,
            n_failures=3,
            position_count=1,
            spot=SAMPLE_SPOT,
            auth_required=True,
            last_error="Authentication required: token expired",
        )
        data = _client_with(svc).get("/api/v1/portfolio/sync/status").json()
        assert data["auth_required"] is True
        assert "token expired" in data["last_error"]

    def test_healthy_sync_reports_no_auth_requirement(self, client):
        data = client.get("/api/v1/portfolio/sync/status").json()
        assert data["auth_required"] is False
        assert data["last_error"] is None
        assert data["spot"] == SAMPLE_SPOT
