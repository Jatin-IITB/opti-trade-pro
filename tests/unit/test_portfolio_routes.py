"""Tests for portfolio REST API routes."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from options_trading.api.routes.portfolio import router as portfolio_router
from options_trading.services.portfolio_client import (
    UpstoxHolding,
    UpstoxOrder,
    UpstoxPosition,
)
from options_trading.services.portfolio_sync_service import PortfolioSyncStatus
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
    contract = OptionContract(
        symbol="NIFTY",
        strike=24500.0,
        expiry=0.05,
        option_type=OptionType.CALL,
        lot_size=50,
    )
    return Portfolio(
        positions=(Position(contract=contract, quantity=50.0, entry_price=180.0),),
        equity=4000.0,
        margin_available=100000.0,
    )


def _mock_sync_service(
    positions: list | None = None,
    holdings: list | None = None,
    orders: list | None = None,
    portfolio: Portfolio | None = None,
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
    svc.status.return_value = PortfolioSyncStatus(
        running=True,
        last_sync_ts=1_724_000_000.0,
        n_syncs=5,
        n_failures=0,
        position_count=1,
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
    return TestClient(app)


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
