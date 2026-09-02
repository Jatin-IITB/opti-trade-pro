"""Periodic portfolio sync: Upstox positions -> analytics -> WebSocket broadcast.

Modeled on ``LivePipelineService`` and ``CaptureScheduler``: periodic fetch,
survive-and-log error handling, in-memory latest-state cache, and WebSocket
broadcast to all connected dashboard clients.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from optitrade.core.types import Portfolio

from ..utils.exceptions import AuthError
from .live_analytics import BookContext
from .portfolio_client import (
    UpstoxFunds,
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
    include_funds: bool = True


@dataclass
class PortfolioSyncStatus:
    running: bool = False
    last_sync_ts: float | None = None
    n_syncs: int = 0
    n_failures: int = 0
    position_count: int = 0
    spot: float | None = None
    #: True when syncs are blocked on an expired/absent Upstox token. Reported
    #: separately from n_failures because retrying cannot clear it — the user
    #: must re-authenticate. Without this the loop logs the same failure every
    #: 60s indefinitely and the UI shows a silently stale book.
    auth_required: bool = False
    last_error: str | None = None


class PortfolioSyncService:
    """Periodic portfolio sync from Upstox -> in-memory state -> WebSocket."""

    def __init__(
        self,
        client: UpstoxPortfolioClient,
        ws_manager: WebSocketManager,
        config: PortfolioSyncConfig = PortfolioSyncConfig(),
        spot_fn: Callable[[], float | None] | None = None,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        """``spot_fn`` supplies the live underlying level.

        Without it there is no spot, and every downstream Greek and moneyness
        label is undefined — so consumers get ``None`` rather than a guess.
        Wire it to the live pipeline's latest snapshot.

        ``now_fn`` is injectable so tests can pin the clock: expiry filtering
        is time-dependent, and a fixture's expiry date would otherwise silently
        go stale (CLAUDE.md: no wall-clock dependence in tests).
        """
        self._client = client
        self._ws_manager = ws_manager
        self._config = config
        self._spot_fn = spot_fn
        self._now_fn = now_fn

        self._latest_portfolio: Portfolio | None = None
        self._latest_positions: list[UpstoxPosition] = []
        self._latest_holdings: list[UpstoxHolding] = []
        self._latest_orders: list[UpstoxOrder] = []
        self._latest_funds: UpstoxFunds | None = None
        self._latest_spot: float | None = None
        self._last_sync_ts: float | None = None
        self._sync_count: int = 0
        self._failure_count: int = 0
        self._auth_required: bool = False
        self._last_error: str | None = None

        self._stop_event = asyncio.Event()
        self._running = False

    def _resolve_spot(self) -> float | None:
        """Current underlying level, or None when no live source is wired."""
        if self._spot_fn is None:
            return None
        try:
            spot = self._spot_fn()
        except Exception:
            logger.debug("spot_fn raised; treating spot as unavailable", exc_info=True)
            return None
        if spot is None or spot <= 0:
            return None
        return float(spot)

    async def sync_once(self) -> None:
        """Fetch positions, map to Portfolio, broadcast update."""
        try:
            positions = await self._client.fetch_positions()
            self._latest_positions = positions

            # Secondary fetches degrade rather than abort the sync — but an
            # AuthError is re-raised, never swallowed. Catching it here would
            # let the success path below clear ``auth_required``, reporting a
            # green, freshly-timestamped book while the token is dead.
            degraded: list[str] = []

            if self._config.include_holdings:
                try:
                    self._latest_holdings = await self._client.fetch_holdings()
                except AuthError:
                    raise
                except Exception as exc:
                    logger.warning("Holdings fetch failed: %s", exc, exc_info=True)
                    # Cleared, not left stale: serving the last-good list
                    # beside a fresh syncTimestamp presents old data as current.
                    self._latest_holdings = []
                    degraded.append("holdings")

            if self._config.include_orders:
                try:
                    self._latest_orders = await self._client.fetch_orders()
                except AuthError:
                    raise
                except Exception as exc:
                    logger.warning("Orders fetch failed: %s", exc, exc_info=True)
                    self._latest_orders = []
                    degraded.append("orders")

            if self._config.include_funds:
                try:
                    self._latest_funds = await self._client.fetch_funds()
                except AuthError:
                    raise
                except Exception as exc:
                    logger.warning("Funds fetch failed: %s", exc, exc_info=True)
                    self._latest_funds = None
                    degraded.append("funds")

            self._latest_spot = self._resolve_spot()
            portfolio = to_core_portfolio(positions, funds=self._latest_funds, now_fn=self._now_fn)
            self._latest_portfolio = portfolio
            self._last_sync_ts = self._now_fn()
            self._sync_count += 1

            self._auth_required = False
            self._last_error = (
                f"Partial sync: {', '.join(degraded)} unavailable" if degraded else None
            )
            payload = self._build_broadcast_payload(positions, portfolio)
            n_sent = await self._ws_manager.send_portfolio_update(payload)
            logger.info(
                "Portfolio sync: %d positions, %d core, %d clients",
                len(positions),
                len(portfolio.positions),
                n_sent,
            )

        except AuthError as exc:
            self._failure_count += 1
            self._auth_required = True
            self._last_error = f"Authentication required: {exc}"
            # Not logged with a stack trace or at exception level: this is an
            # expected daily event, not a defect, and it repeats every cycle
            # until the user logs in again.
            logger.warning("Portfolio sync blocked: Upstox re-authentication required (%s)", exc)
        except Exception as exc:
            self._failure_count += 1
            self._last_error = f"{type(exc).__name__}: {exc}"
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
        funds = self._latest_funds
        return {
            "positions": position_list,
            "summary": {
                "totalPositions": len(positions),
                "corePositions": len(portfolio.positions),
                "totalPnl": total_pnl,
                "equity": portfolio.equity if funds is not None else None,
                "marginUsed": funds.used_margin if funds is not None else None,
                "marginAvailable": funds.available_margin if funds is not None else None,
                "marginUtilization": funds.margin_utilization if funds is not None else None,
                "spot": self._latest_spot,
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

    def get_latest_funds(self) -> UpstoxFunds | None:
        return self._latest_funds

    def get_latest_spot(self) -> float | None:
        """Underlying level as of the last sync, or None if no source is wired."""
        return self._latest_spot

    def get_book_context(self) -> BookContext | None:
        """The synced book packaged for the analytics pipeline.

        Marks are keyed by trading symbol, matching ``OptionContract.symbol``
        as set by ``to_core_portfolio`` — that key agreement is what lets the
        analytics price each leg against its own current price.
        """
        portfolio = self._latest_portfolio
        if portfolio is None or not portfolio.positions:
            return None
        funds = self._latest_funds
        return BookContext(
            portfolio=portfolio,
            marks={p.trading_symbol: p.last_price for p in self._latest_positions},
            equity=funds.total_equity if funds is not None else None,
            margin_used=funds.used_margin if funds is not None else None,
            margin_available=funds.available_margin if funds is not None else None,
        )

    def status(self) -> PortfolioSyncStatus:
        return PortfolioSyncStatus(
            running=self._running,
            last_sync_ts=self._last_sync_ts,
            n_syncs=self._sync_count,
            n_failures=self._failure_count,
            position_count=len(self._latest_positions),
            spot=self._latest_spot,
            auth_required=self._auth_required,
            last_error=self._last_error,
        )


__all__ = ["PortfolioSyncConfig", "PortfolioSyncService", "PortfolioSyncStatus"]
