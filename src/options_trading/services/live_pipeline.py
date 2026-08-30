"""Orchestration: capture callback → analytics → WebSocket broadcast.

Ties the capture scheduler, quant engines, and WebSocket manager into a single
pipeline that pushes live dashboard data to all connected frontends on every
successful capture cycle.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from optitrade.data.models import RawChain

from .capture_service import CaptureReport
from .live_analytics import LiveAnalytics, LiveAnalyticsConfig, LiveDashboardPayload
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
    ) -> None:
        self._ws_manager = ws_manager
        self._config = config
        self._analytics = LiveAnalytics(LiveAnalyticsConfig(vol_model=config.vol_model))
        self._last_payload: LiveDashboardPayload | None = None
        self._last_chain: RawChain | None = None

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
        try:
            payload = await asyncio.to_thread(self._analytics.build_from_raw_chain, chain)
        except Exception:
            logger.exception("Live analytics pipeline failed for %s", chain.underlying)
            return

        self._last_payload = payload
        n_sent = await self._ws_manager.send_dashboard_update(
            {
                "volSurface": payload.vol_surface,
                "optionChain": payload.option_chain,
                "greeksComparison": payload.greeks_book,
                "essviCalibration": payload.essvi_calibration,
                "riskDashboard": payload.risk_dashboard,
                "timestamp": payload.timestamp,
                "underlying": payload.underlying,
                "spot": payload.spot,
            }
        )
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


__all__ = ["LivePipelineConfig", "LivePipelineService"]
