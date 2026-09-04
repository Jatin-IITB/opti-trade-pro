"""Orchestration: capture callback → analytics → WebSocket broadcast.

Ties the capture scheduler, quant engines, and WebSocket manager into a single
pipeline that pushes live dashboard data to all connected frontends on every
successful capture cycle.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from optitrade.data import SnapshotStore
from optitrade.data.models import RawChain

from .analyst_service import AnalystService, unavailable_analysts_wire
from .capture_scheduler import ScheduleConfig, is_market_open
from .capture_service import CaptureReport
from .desk_service import DeskService, unavailable_desk_wire
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
    #: Exchange calendar, used only to describe a restored snapshot. It says
    #: whether a stored capture is a session's final one or was taken well
    #: before the close, which is the difference between "closing prices" and
    #: "the last prices we happened to catch".
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)


def describe_stored_snapshot(
    snapshot_ts: float,
    now: float,
    config: ScheduleConfig,
) -> str:
    """One sentence naming which session's prices a restored payload holds.

    Composed here rather than in the browser because the exchange calendar is
    server-side, and because the honest wording depends on a fact only this
    side knows: whether the snapshot is the session's last capture or one
    taken well before the close. Calling a 14:02 capture "closing prices"
    would be false, and it is exactly the kind of falsehood that survives
    review because it reads so naturally.

    ``now`` is injected rather than read from the clock so the wording is
    testable at fixed instants.
    """
    zone = ZoneInfo(config.timezone)
    stamp = datetime.fromtimestamp(snapshot_ts, tz=zone)
    when = f"{stamp:%a %-d %b %Y, %H:%M} {stamp:%Z}"

    if is_market_open(now, config):
        return (
            f"No capture has run yet this session. These are the last stored prices, from {when}."
        )

    close_time = datetime.strptime(config.market_close, "%H:%M").time()
    close = datetime.combine(stamp.date(), close_time, tzinfo=zone)
    shortfall = (close - stamp).total_seconds()

    # Within one capture interval of the close, this *is* the session's last
    # word: no further capture was due before trading stopped.
    if shortfall <= config.interval_seconds:
        return f"Market closed. These are the session's closing prices, from {when}."

    return (
        f"Market closed. These are the last prices captured, at {when} — "
        f"{int(shortfall // 60)} minutes before the {config.market_close} close, "
        f"so the session's final moves are not included."
    )


class LivePipelineService:
    """Post-capture callback that builds analytics and broadcasts to dashboards."""

    def __init__(
        self,
        ws_manager: WebSocketManager,
        config: LivePipelineConfig = LivePipelineConfig(),
        book_fn: Callable[[], BookContext | None] | None = None,
        history: HistoryAnalytics | None = None,
        desk: DeskService | None = None,
        analysts: AnalystService | None = None,
        store: SnapshotStore | None = None,
    ) -> None:
        """``book_fn`` supplies the user's synced book, if any.

        Injected rather than imported so the pipeline does not depend on the
        portfolio sync: with no book, the book-shaped panels report that they
        have none instead of inventing one.

        ``history`` supplies the replay-backed panels. It is separately
        cached and far more expensive than the per-chain builders, so it is
        merged into the same broadcast rather than given its own cadence —
        the cost is paid at most once per refresh interval, not per tick.

        ``desk`` supplies the paper-desk panel. Only its cheap *read* is on
        this path: broadcasting must never advance the desk, because an
        advance takes paper fills and mutates a persisted book. A trade that
        happened because a dashboard ticked would be a trade nobody chose.

        ``analysts`` supplies the analyst panel. Read-only against the same
        journal the desk writes, so it is safe on this path for the same
        reason the desk read is.

        ``store`` enables :meth:`warm_start_from_store`. Without it the
        dashboard stays empty until the first capture of the process, which
        outside market hours means empty until the next trading day.
        """
        self._ws_manager = ws_manager
        self._config = config
        self._analytics = LiveAnalytics(LiveAnalyticsConfig(vol_model=config.vol_model))
        self._book_fn = book_fn
        self._history = history
        self._desk = desk
        self._analysts = analysts
        self._store = store
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

        # Same contract for the desk: the key is always present, and a
        # failure reports an unavailable desk with the kill switch shown as
        # engaged rather than leaving the last good book on screen.
        try:
            desk = await self._desk.build_async() if self._desk is not None else None
        except Exception:
            logger.exception("Desk state failed; reporting the desk as unavailable")
            desk = None
        wire["desk"] = (
            desk.to_wire_dict()
            if desk is not None
            else unavailable_desk_wire(
                "The paper desk state could not be read. The book is not shown "
                "rather than shown as empty; check the server logs."
            )
        )

        # Same contract for the analysts, and it matters most here: analyst
        # output is prose asserting numbers. Leaving the last good reports on
        # screen would keep sentences about a journal this process can no
        # longer read, each still wearing its grounded badge from the previous
        # audit. The key is therefore always present.
        try:
            analysts = await self._analysts.build_async() if self._analysts is not None else None
        except Exception:
            logger.exception("Analyst report failed; reporting the panel as unavailable")
            analysts = None
        wire["analysts"] = (
            analysts.to_wire_dict()
            if analysts is not None
            else unavailable_analysts_wire(
                "The analyst reports could not be produced. No analyst prose is shown "
                "rather than stale prose; check the server logs."
            )
        )
        return wire

    def cache_chain(self, chain: RawChain) -> None:
        """Cache the latest RawChain from a capture cycle for analytics."""
        self._last_chain = chain

    async def warm_start_from_store(self, now: float | None = None) -> bool:
        """Seed the dashboard from the newest stored capture, flagged not-live.

        The live payload lives only in this process's memory, so a restart
        discards it even though the captures themselves are on disk. Outside
        market hours — most of the week — the next capture that could refill
        it is a trading day away, and the dashboard would show nothing at all
        while a full chain sat in the snapshot store unread.

        The restored payload is flagged ``is_live=False`` and carries a note
        naming the session it describes. It is deliberately **not** cached as
        ``_last_chain``: ``on_capture`` rebuilds from that field, so seeding it
        would let a capture cycle that never called ``cache_chain`` rebroadcast
        this old chain as live. Warm start feeds the display path only.

        Best effort — a failure here must never stop the app serving, so it
        returns False and leaves the dashboard empty rather than raising.
        """
        if self._store is None:
            return False
        try:
            paths = self._store.list_snapshots(self._config.underlying)
        except Exception:
            logger.exception("Could not list stored snapshots; dashboard starts empty")
            return False
        if not paths:
            logger.info(
                "No stored capture for %s yet; dashboard starts empty until the "
                "first capture of this session",
                self._config.underlying,
            )
            return False

        newest = paths[-1]
        try:
            chain = await asyncio.to_thread(self._store.read, newest)
            payload = await asyncio.to_thread(
                self._analytics.build_from_raw_chain, chain, self._current_book()
            )
        except Exception:
            logger.exception("Could not restore %s; dashboard starts empty", newest)
            return False

        self._last_payload = replace(
            payload,
            is_live=False,
            as_of_note=describe_stored_snapshot(
                chain.timestamp,
                time.time() if now is None else now,
                self._config.schedule,
            ),
        )
        logger.info(
            "Dashboard warm-started from %s (spot %.1f), flagged not-live",
            newest.name,
            chain.spot,
        )
        return True

    def get_latest_snapshot(self) -> LiveDashboardPayload | None:
        """Return the most recent payload, or None if nothing has been built yet.

        May be a warm-started payload describing a past instant; callers that
        render it must respect its ``is_live`` flag.
        """
        return self._last_payload

    def get_latest_spot(self) -> float | None:
        """Underlying level from the most recent *live* capture, else None.

        This is the app's single source of live spot. Portfolio Greeks and
        moneyness depend on it, so it returns None rather than 0.0 when no
        capture has run — callers must treat "unknown" differently from "zero".

        A warm-started payload is explicitly excluded. Its spot is real but
        old, and pricing today's book against a previous session's underlying
        would relabel moneyness and misstate every Greek while looking
        entirely normal — the same class of error as pricing a 24500 strike
        against a spot of a few thousand. Display may be stale; the money path
        may not.
        """
        payload = self._last_payload
        if payload is None or not payload.is_live or payload.spot <= 0:
            return None
        return payload.spot


__all__ = ["LivePipelineConfig", "LivePipelineService", "describe_stored_snapshot"]
