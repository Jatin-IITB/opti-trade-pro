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
from typing import Any

from optitrade.data.models import RawChain

from .capture_service import CaptureReport
from .history_analytics import HistoryAnalytics, unavailable_history_wire
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
        history: HistoryAnalytics | None = None,
    ) -> None:
        """``book_fn`` supplies the user's synced book, if any.

        Injected rather than imported so the pipeline does not depend on the
        portfolio sync: with no book, the book-shaped panels report that they
        have none instead of inventing one.

        ``history`` supplies the replay-backed panels. It is separately
        cached and far more expensive than the per-chain builders, so it is
        merged into the same broadcast rather than given its own cadence —
        the cost is paid at most once per refresh interval, not per tick.
        """
        self._ws_manager = ws_manager
        self._config = config
        self._analytics = LiveAnalytics(LiveAnalyticsConfig(vol_model=config.vol_model))
        self._book_fn = book_fn
        self._history = history
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
        n_sent = await self._ws_manager.send_dashboard_update(await self.build_wire_dict(payload))
        logger.info(
            "Live dashboard broadcast: %s spot=%.1f, %d clients",
            chain.underlying,
            chain.spot,
            n_sent,
        )

    async def build_wire_dict(self, payload: LiveDashboardPayload) -> dict[str, Any]:
        """The full wire payload: live panels plus the history-backed ones.

        Every delivery path goes through here so the push and pull paths
        cannot disagree about which panels exist.

        A history failure **must still emit the history keys**, carrying an
        explicit unavailable state. Omitting them fails *open*: the frontend
        merges only the keys it receives, so an absent key leaves whatever was
        there before — on a fresh load, the bundled demo curve. A crash in the
        replay would therefore put a fabricated equity curve back on screen
        beside a live spot and timestamp, which is the exact failure this
        phase exists to remove (ADR-008: an error converts to a rejection,
        never a pass-through).
        """
        wire = payload.to_wire_dict()
        try:
            history = await self._history.build_async() if self._history is not None else None
        except Exception:
            logger.exception("History analytics failed; reporting the panels as unavailable")
            history = None
        wire.update(
            history.to_wire_dict()
            if history is not None
            else unavailable_history_wire(
                "The history analytics could not be computed. The panel is "
                "blank rather than showing a placeholder; check the server logs."
            )
        )
        return wire

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
