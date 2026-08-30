"""Tests for the portfolio sync service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from options_trading.services.portfolio_client import (
    UpstoxHolding,
    UpstoxOrder,
    UpstoxPosition,
)
from options_trading.services.portfolio_sync_service import (
    PortfolioSyncConfig,
    PortfolioSyncService,
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


@pytest.fixture()
def client():
    c = MagicMock()
    c.fetch_positions = AsyncMock(return_value=[_sample_position()])
    c.fetch_holdings = AsyncMock(return_value=[_sample_holding()])
    c.fetch_orders = AsyncMock(return_value=[_sample_order()])
    return c


@pytest.fixture()
def ws_manager():
    mgr = MagicMock()
    mgr.send_portfolio_update = AsyncMock(return_value=1)
    return mgr


@pytest.fixture()
def service(client, ws_manager):
    return PortfolioSyncService(
        client=client,
        ws_manager=ws_manager,
        config=PortfolioSyncConfig(sync_interval_seconds=60),
        spot_fn=lambda: 24500.0,
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
