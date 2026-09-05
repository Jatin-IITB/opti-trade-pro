"""Tests for the portfolio sync service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from options_trading.services.portfolio_client import (
    UpstoxFunds,
    UpstoxHolding,
    UpstoxOrder,
    UpstoxPosition,
)
from options_trading.services.portfolio_sync_service import (
    PortfolioSyncConfig,
    PortfolioSyncService,
)
from options_trading.utils.exceptions import AuthError, NetworkError


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


@pytest.fixture()
def client():
    c = MagicMock()
    c.fetch_positions = AsyncMock(return_value=[_sample_position()])
    c.fetch_holdings = AsyncMock(return_value=[_sample_holding()])
    c.fetch_orders = AsyncMock(return_value=[_sample_order()])
    c.fetch_funds = AsyncMock(
        return_value=UpstoxFunds(used_margin=45_000.0, available_margin=155_000.0)
    )
    return c


@pytest.fixture()
def ws_manager():
    mgr = MagicMock()
    mgr.send_portfolio_update = AsyncMock(return_value=1)
    return mgr


# Pinned epoch: 2024-08-18, comfortably before the fixtures' 2025-09-07
# expiry. Expiry filtering is time-dependent, so the clock must be pinned or
# these fixtures silently go stale once that date passes.
PINNED_NOW = 1_724_000_000.0


@pytest.fixture()
def service(client, ws_manager):
    return PortfolioSyncService(
        client=client,
        ws_manager=ws_manager,
        config=PortfolioSyncConfig(sync_interval_seconds=60),
        spot_fn=lambda: 24500.0,
        now_fn=lambda: PINNED_NOW,
    )


class TestPortfolioSyncService:
    def test_no_portfolio_initially(self, service):
        assert service.get_latest_portfolio() is None
        assert service.get_latest_positions() == []

    @pytest.mark.asyncio
    async def test_sync_once_fetches_and_broadcasts(self, service, client, ws_manager):
        await service.sync_once()

        client.fetch_positions.assert_awaited_once()
        client.fetch_holdings.assert_awaited_once()
        client.fetch_orders.assert_awaited_once()
        ws_manager.send_portfolio_update.assert_awaited_once()

        payload = ws_manager.send_portfolio_update.call_args[0][0]
        assert "positions" in payload
        assert "summary" in payload
        assert payload["summary"]["totalPositions"] == 1

    @pytest.mark.asyncio
    async def test_sync_once_stores_portfolio(self, service):
        await service.sync_once()

        portfolio = service.get_latest_portfolio()
        assert portfolio is not None
        assert len(portfolio.positions) == 1

    @pytest.mark.asyncio
    async def test_sync_once_stores_positions(self, service):
        await service.sync_once()

        positions = service.get_latest_positions()
        assert len(positions) == 1
        assert positions[0].trading_symbol == "NIFTY2490724500CE"

    @pytest.mark.asyncio
    async def test_sync_once_stores_holdings(self, service):
        await service.sync_once()

        holdings = service.get_latest_holdings()
        assert len(holdings) == 1
        assert holdings[0].trading_symbol == "RELIANCE"

    @pytest.mark.asyncio
    async def test_sync_once_stores_orders(self, service):
        await service.sync_once()

        orders = service.get_latest_orders()
        assert len(orders) == 1
        assert orders[0].order_id == "240830000001"

    @pytest.mark.asyncio
    async def test_sync_once_updates_status(self, service):
        await service.sync_once()

        status = service.status()
        assert status.n_syncs == 1
        assert status.n_failures == 0
        assert status.position_count == 1
        assert status.last_sync_ts is not None

    @pytest.mark.asyncio
    async def test_sync_once_survives_client_failure(self, service, client, ws_manager):
        client.fetch_positions = AsyncMock(side_effect=RuntimeError("API down"))

        await service.sync_once()

        ws_manager.send_portfolio_update.assert_not_awaited()
        assert service.get_latest_portfolio() is None
        status = service.status()
        assert status.n_failures == 1
        assert status.n_syncs == 0

    @pytest.mark.asyncio
    async def test_sync_once_survives_holdings_failure(self, service, client):
        client.fetch_holdings = AsyncMock(side_effect=RuntimeError("Holdings API down"))

        await service.sync_once()

        portfolio = service.get_latest_portfolio()
        assert portfolio is not None
        assert service.get_latest_holdings() == []

    @pytest.mark.asyncio
    async def test_multiple_syncs_increment_count(self, service):
        await service.sync_once()
        await service.sync_once()

        status = service.status()
        assert status.n_syncs == 2

    def test_stop_sets_event(self, service):
        service.stop()
        assert service._stop_event.is_set()

    @pytest.mark.asyncio
    async def test_broadcast_payload_shape(self, service, ws_manager):
        await service.sync_once()

        payload = ws_manager.send_portfolio_update.call_args[0][0]
        assert isinstance(payload["positions"], list)
        assert len(payload["positions"]) == 1

        pos = payload["positions"][0]
        assert "instrumentKey" in pos
        assert "tradingSymbol" in pos
        assert "quantity" in pos
        assert "buyPrice" in pos
        assert "pnl" in pos

        summary = payload["summary"]
        assert "totalPositions" in summary
        assert "corePositions" in summary
        assert "totalPnl" in summary
        assert summary["totalPnl"] == 4000.0


class TestAuthExpiry:
    """An expired Upstox token must be reported, not retried into the void.

    Tokens expire daily and standard apps get no refresh token, so every
    unattended session eventually hits this. Before, the loop logged an
    identical exception every 60s forever and the UI showed a stale book with
    no indication anything was wrong.
    """

    @pytest.mark.asyncio
    async def test_auth_error_sets_auth_required(self, service, client):
        client.fetch_positions = AsyncMock(side_effect=AuthError("token expired"))
        await service.sync_once()

        status = service.status()
        assert status.auth_required is True
        assert status.n_failures == 1
        assert "Authentication required" in (status.last_error or "")

    @pytest.mark.asyncio
    async def test_transient_error_does_not_set_auth_required(self, service, client):
        client.fetch_positions = AsyncMock(side_effect=NetworkError("connection reset"))
        await service.sync_once()

        status = service.status()
        assert status.auth_required is False
        assert status.n_failures == 1
        assert "NetworkError" in (status.last_error or "")

    @pytest.mark.asyncio
    async def test_successful_sync_clears_auth_required(self, service, client):
        client.fetch_positions = AsyncMock(side_effect=AuthError("token expired"))
        await service.sync_once()
        assert service.status().auth_required is True

        client.fetch_positions = AsyncMock(return_value=[_sample_position()])
        await service.sync_once()

        status = service.status()
        assert status.auth_required is False
        assert status.last_error is None

    @pytest.mark.asyncio
    async def test_sync_survives_auth_error(self, service, client):
        """Survive-and-log still applies: the loop must not die."""
        client.fetch_positions = AsyncMock(side_effect=AuthError("token expired"))
        await service.sync_once()
        await service.sync_once()
        assert service.status().n_failures == 2


class TestFunds:
    @pytest.mark.asyncio
    async def test_equity_and_margin_come_from_funds(self, service, ws_manager):
        await service.sync_once()
        summary = ws_manager.send_portfolio_update.call_args[0][0]["summary"]
        assert summary["equity"] == 200_000.0
        assert summary["marginUsed"] == 45_000.0
        assert summary["marginAvailable"] == 155_000.0
        assert summary["equity"] != summary["totalPnl"]

    @pytest.mark.asyncio
    async def test_funds_failure_leaves_equity_null_not_zero(self, service, client, ws_manager):
        client.fetch_funds = AsyncMock(side_effect=NetworkError("funds unavailable"))
        await service.sync_once()

        summary = ws_manager.send_portfolio_update.call_args[0][0]["summary"]
        assert summary["equity"] is None
        assert summary["marginUsed"] is None
        # Positions still sync: a funds outage must not blank the book.
        assert summary["totalPositions"] == 1

    @pytest.mark.asyncio
    async def test_spot_is_recorded(self, service):
        await service.sync_once()
        assert service.get_latest_spot() == 24500.0
        assert service.status().spot == 24500.0
