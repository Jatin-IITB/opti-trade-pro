"""Periodic portfolio sync: Upstox positions → analytics → WebSocket broadcast.

Modeled on ``LivePipelineService`` and ``CaptureScheduler``: periodic fetch,
survive-and-log error handling, in-memory latest-state cache, and WebSocket
broadcast to all connected dashboard clients.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from optitrade.core.types import Portfolio

from .portfolio_client import (
    UpstoxHolding,
    UpstoxOrder,
    UpstoxPortfolioClient,
    UpstoxPosition,
    to_core_portfolio,
)
from .websocket_manager import WebSocketManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PortfolioSyncConfig:
    sync_interval_seconds: int = 60
    include_holdings: bool = True
    include_orders: bool = True


@dataclass
class PortfolioSyncStatus:
    running: bool = False
    last_sync_ts: float | None = None
    n_syncs: int = 0
    n_failures: int = 0
    position_count: int = 0


class PortfolioSyncService:
    """Periodic portfolio sync from Upstox → in-memory state → WebSocket."""

    def __init__(
        self,
        client: UpstoxPortfolioClient,
        ws_manager: WebSocketManager,
        config: PortfolioSyncConfig = PortfolioSyncConfig(),
        spot_fn: callable | None = None,
    ) -> None:
        self._client = client
        self._ws_manager = ws_manager
        self._config = config
        self._spot_fn = spot_fn

        self._latest_portfolio: Portfolio | None = None
        self._latest_positions: list[UpstoxPosition] = []
        self._latest_holdings: list[UpstoxHolding] = []
        self._latest_orders: list[UpstoxOrder] = []
        self._last_sync_ts: float | None = None
        self._sync_count: int = 0
        self._failure_count: int = 0

        self._stop_event = asyncio.Event()
        self._running = False

    async def sync_once(self) -> None:
        """Fetch positions, map to Portfolio, broadcast update."""
        try:
            positions = await self._client.fetch_positions()
            self._latest_positions = positions

            if self._config.include_holdings:
                try:
                    self._latest_holdings = await self._client.fetch_holdings()
                except Exception:
                    logger.warning("Holdings fetch failed, continuing with positions only")

            if self._config.include_orders:
                try:
                    self._latest_orders = await self._client.fetch_orders()
                except Exception:
                    logger.warning("Orders fetch failed, continuing without orders")

            spot = self._spot_fn() if self._spot_fn else 0.0
            portfolio = to_core_portfolio(positions, spot=spot)
            self._latest_portfolio = portfolio
            self._last_sync_ts = time.time()
            self._sync_count += 1

            payload = self._build_broadcast_payload(positions, portfolio)
            n_sent = await self._ws_manager.send_portfolio_update(payload)
            logger.info(
                "Portfolio sync: %d positions, %d core, %d clients",
                len(positions),
                len(portfolio.positions),
                n_sent,
            )

        except Exception:
            self._failure_count += 1
            logger.exception("Portfolio sync failed (attempt %d)", self._failure_count)

    def _build_broadcast_payload(
        self, positions: list[UpstoxPosition], portfolio: Portfolio
    ) -> dict:
        position_list = []
        for pos in positions:
            position_list.append(
                {
                    "instrumentKey": pos.instrument_key,
                    "tradingSymbol": pos.trading_symbol,
                    "exchange": pos.exchange,
                    "product": pos.product,
                    "quantity": pos.quantity,
                    "buyPrice": pos.buy_price,
                    "sellPrice": pos.sell_price,
                    "lastPrice": pos.last_price,
                    "pnl": pos.pnl,
                    "optionType": pos.option_type,
                    "strikePrice": pos.strike_price,
                    "expiry": pos.expiry,
                }
            )

        total_pnl = sum(p.pnl for p in positions)
        return {
            "positions": position_list,
            "summary": {
                "totalPositions": len(positions),
                "corePositions": len(portfolio.positions),
                "totalPnl": total_pnl,
                "equity": portfolio.equity,
            },
            "syncTimestamp": self._last_sync_ts,
        }

    async def run(self) -> None:
        """Periodic sync loop. Stops when ``stop()`` is called."""
        self._running = True
        logger.info("Portfolio sync started (interval=%ds)", self._config.sync_interval_seconds)
        try:
            while not self._stop_event.is_set():
                await self.sync_once()
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._config.sync_interval_seconds,
                    )
                except TimeoutError:
                    pass
        finally:
            self._running = False
            logger.info("Portfolio sync stopped")

    def stop(self) -> None:
        self._stop_event.set()

    def get_latest_portfolio(self) -> Portfolio | None:
        return self._latest_portfolio

    def get_latest_positions(self) -> list[UpstoxPosition]:
        return list(self._latest_positions)

    def get_latest_holdings(self) -> list[UpstoxHolding]:
        return list(self._latest_holdings)

    def get_latest_orders(self) -> list[UpstoxOrder]:
        return list(self._latest_orders)

    def status(self) -> PortfolioSyncStatus:
        return PortfolioSyncStatus(
            running=self._running,
            last_sync_ts=self._last_sync_ts,
            n_syncs=self._sync_count,
            n_failures=self._failure_count,
            position_count=len(self._latest_positions),
        )


__all__ = ["PortfolioSyncConfig", "PortfolioSyncService", "PortfolioSyncStatus"]
