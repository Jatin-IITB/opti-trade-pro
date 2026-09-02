"""Orchestration: capture callback → analytics → WebSocket broadcast.

Ties the capture scheduler, quant engines, and WebSocket manager into a single
pipeline that pushes live dashboard data to all connected frontends on every
successful capture cycle.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from optitrade.data.models import RawChain

from .capture_service import CaptureReport
from .live_analytics import (
    BookContext,
    LiveAnalytics,
    LiveAnalyticsConfig,
    LiveDashboardPayload,
)
from .websocket_manager import WebSocketManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LivePipelineConfig:
    underlying: str = "NIFTY"
    vol_model: str = "essvi"


class LivePipelineService:
    """Post-capture callback that builds analytics and broadcasts to dashboards."""

    def __init__(
        self,
        ws_manager: WebSocketManager,
        config: LivePipelineConfig = LivePipelineConfig(),
        book_fn: Callable[[], BookContext | None] | None = None,
    ) -> None:
        """``book_fn`` supplies the user's synced book, if any.

        Injected rather than imported so the pipeline does not depend on the
        portfolio sync: with no book, the book-shaped panels report that they
        have none instead of inventing one.
        """
        self._ws_manager = ws_manager
        self._config = config
        self._analytics = LiveAnalytics(LiveAnalyticsConfig(vol_model=config.vol_model))
        self._book_fn = book_fn
        self._last_payload: LiveDashboardPayload | None = None
        self._last_chain: RawChain | None = None

    def _current_book(self) -> BookContext | None:
        if self._book_fn is None:
            return None
        try:
            return self._book_fn()
        except Exception:
            logger.exception("book_fn failed; book panels will be empty this cycle")
            return None

    async def on_capture(self, report: CaptureReport) -> None:
        """Post-capture callback: re-fetch chain, run analytics, broadcast.

        Called by the ``CaptureScheduler`` after every successful capture. The
        capture just proved broker connectivity, so a re-fetch is reliable. The
        analytics run in a worker thread to keep the event loop responsive.
        """
        if self._last_chain is None:
            logger.info("on_capture called but no chain cached yet; skipping broadcast")
            return

        chain = self._last_chain
        book = self._current_book()
        try:
            payload = await asyncio.to_thread(self._analytics.build_from_raw_chain, chain, book)
        except Exception:
            logger.exception("Live analytics pipeline failed for %s", chain.underlying)
            return

        self._last_payload = payload
        n_sent = await self._ws_manager.send_dashboard_update(payload.to_wire_dict())
        logger.info(
            "Live dashboard broadcast: %s spot=%.1f, %d clients",
            chain.underlying,
            chain.spot,
            n_sent,
        )

    def cache_chain(self, chain: RawChain) -> None:
        """Cache the latest RawChain from a capture cycle for analytics."""
        self._last_chain = chain

    def get_latest_snapshot(self) -> LiveDashboardPayload | None:
        """Return the most recent payload, or None if no capture has run yet."""
        return self._last_payload

    def get_latest_spot(self) -> float | None:
        """Underlying level from the most recent capture, or None before the first.

        This is the app's single source of live spot. Portfolio Greeks and
        moneyness depend on it, so it returns None rather than 0.0 when no
        capture has run — callers must treat "unknown" differently from "zero".
        """
        if self._last_payload is None or self._last_payload.spot <= 0:
            return None
        return self._last_payload.spot


__all__ = ["LivePipelineConfig", "LivePipelineService"]
