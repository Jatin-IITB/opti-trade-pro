"""Capture schedule lifecycle: target resolution, start, and auto-start.

The capture scheduler is the only source of live analytics data in the app:
it fetches a chain, persists a filtered snapshot, and hands the chain to
``LivePipelineService``, which runs the quant engines and broadcasts to
connected dashboards. If no schedule runs, every analytics tab falls back to
bundled demo data — so this module exists to make the schedule start on its
own once a valid token is available, rather than requiring an undocumented
manual API call.

Target resolution (which instrument, which expiry) lives here so the REST
route and the startup path cannot drift apart.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from optitrade.data import SnapshotStore

from ..api.tools.instrument_key import get_instrument_key_async
from ..api.tools.live_contracts import fetch_live_option_expiries
from ..config.settings import settings
from ..utils.exceptions import DataQualityError
from .capture_scheduler import CaptureScheduler, ScheduleConfig
from .capture_service import CaptureReport, UpstoxCaptureSource, capture_and_store
from .token_provider import TokenProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CaptureTarget:
    """One resolved capture target: what to capture and how often."""

    underlying: str
    instrument_key: str
    expiry_date: str
    interval_seconds: int


async def resolve_autostart_target(access_token: str) -> CaptureTarget:
    """Resolve the configured underlying to a live instrument key and expiry.

    Raises ``DataQualityError`` if the instrument cannot be resolved or the
    configured expiry index is past the end of the tradable ladder — the
    caller logs and skips autostart rather than capturing the wrong contract.
    """
    underlying = settings.capture_autostart_underlying
    instrument_key = await get_instrument_key_async(settings.trading_symbol, settings.exchange)
    if not instrument_key:
        raise DataQualityError(
            f"Cannot resolve instrument key for {settings.trading_symbol}/{settings.exchange}"
        )

    expiries = await asyncio.to_thread(fetch_live_option_expiries, instrument_key, access_token)
    index = settings.capture_autostart_expiry_index
    if index >= len(expiries):
        raise DataQualityError(
            f"capture_autostart_expiry_index={index} but only {len(expiries)} "
            f"expiries are tradable for {underlying}"
        )

    return CaptureTarget(
        underlying=underlying,
        instrument_key=instrument_key,
        expiry_date=expiries[index],
        interval_seconds=settings.capture_interval_seconds,
    )


def is_schedule_running(app) -> bool:
    task = getattr(app.state, "capture_scheduler_task", None)
    return task is not None and not task.done()


def start_schedule(
    app,
    target: CaptureTarget,
    token_provider: TokenProvider,
) -> CaptureScheduler:
    """Build and launch a capture scheduler for ``target`` on ``app``.

    The caller must check :func:`is_schedule_running` first; starting a second
    scheduler would double the broker call rate against the same instrument.

    The token is resolved on the event loop before each capture (via
    ``before_capture``) and read synchronously inside the worker thread, so a
    schedule spanning the daily Upstox token expiry keeps running.
    """
    source = UpstoxCaptureSource(
        token_fn=token_provider.cached,
        instrument_key=target.instrument_key,
        expiry_date=target.expiry_date,
    )
    store = SnapshotStore(Path(settings.snapshot_store_path))
    pipeline = getattr(app.state, "live_pipeline", None)

    def capture_once() -> CaptureReport:
        # One fetch serves both the stored snapshot and the analytics, so the
        # persisted history and the broadcast dashboard describe the same
        # instant — and the broker sees one call per cycle, not two.
        chain = source.fetch_chain(target.underlying)
        report = capture_and_store(source, store, target.underlying, chain=chain)
        if pipeline is not None:
            pipeline.cache_chain(chain)
        return report

    async def refresh_token() -> None:
        await token_provider.get()

    scheduler = CaptureScheduler(
        capture_fn=capture_once,
        config=ScheduleConfig(interval_seconds=target.interval_seconds),
        on_capture=pipeline.on_capture if pipeline is not None else None,
        before_capture=refresh_token,
    )
    app.state.capture_scheduler = scheduler
    app.state.capture_scheduler_task = asyncio.create_task(scheduler.run())
    app.state.capture_target = target
    logger.info(
        "Capture scheduler started: %s %s expiry %s every %ss",
        target.underlying,
        target.instrument_key,
        target.expiry_date,
        target.interval_seconds,
    )
    return scheduler


async def autostart_if_configured(app, token_provider: TokenProvider) -> bool:
    """Start the capture schedule on the nearest expiry, if enabled.

    Best effort by design: a failure here must not prevent the app from
    serving. Returns True only if a scheduler was actually started.
    """
    if not settings.capture_autostart:
        logger.info("Capture autostart disabled (settings.capture_autostart=False)")
        return False
    if is_schedule_running(app):
        logger.debug("Capture scheduler already running; autostart skipped")
        return False

    try:
        access_token = await token_provider.get()
        target = await resolve_autostart_target(access_token)
    except Exception as exc:
        logger.warning(
            "Capture autostart skipped: could not resolve a target (%s). "
            "Start one manually via POST /api/v1/capture/schedule/start",
            exc,
        )
        return False

    try:
        start_schedule(app, target, token_provider)
    except Exception:
        logger.exception("Capture autostart failed to start the scheduler")
        return False
    return True


__all__ = [
    "CaptureTarget",
    "autostart_if_configured",
    "is_schedule_running",
    "resolve_autostart_target",
    "start_schedule",
]
