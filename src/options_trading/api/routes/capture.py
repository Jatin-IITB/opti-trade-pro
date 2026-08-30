# src/options_trading/api/routes/capture.py
"""Chain-capture endpoints: live Upstox chains -> quote filters -> Parquet snapshots."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from optitrade.data import SnapshotStore

from ...config.settings import settings
from ...services.auth_service import AuthService
from ...services.capture_scheduler import CaptureScheduler, ScheduleConfig, SchedulerStatus
from ...services.capture_service import CaptureReport, UpstoxCaptureSource, capture_and_store
from ...utils.exceptions import DataQualityError
from ..dependencies import get_auth_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/capture", tags=["Capture"])


class CaptureRunRequest(BaseModel):
    """Parameters for one capture run against the live Upstox chain API."""

    underlying: str = Field(description="Underlying name used for the snapshot store, e.g. NIFTY")
    instrument_key: str = Field(description="Upstox instrument key, e.g. 'NSE_INDEX|Nifty 50'")
    expiry_date: str = Field(description="Option expiry date, YYYY-MM-DD")


async def get_access_token(auth_service: AuthService = Depends(get_auth_service)) -> str:
    """Resolve a valid Upstox access token from the app's AuthService, or 401."""
    try:
        async with auth_service as auth:
            return await auth.get_valid_access_token()
    except Exception as exc:
        logger.warning("Capture request rejected: no valid access token (%s)", exc)
        raise HTTPException(
            status_code=401, detail="Not authenticated with Upstox; log in via /api/v1/auth/login"
        ) from exc


def _snapshot_store() -> SnapshotStore:
    return SnapshotStore(Path(settings.snapshot_store_path))


@router.post("/run")
async def run_capture(
    body: CaptureRunRequest, access_token: str = Depends(get_access_token)
) -> dict[str, Any]:
    """Capture one live chain, filter it, and persist the clean snapshot."""
    try:
        source = UpstoxCaptureSource(
            access_token=access_token,
            instrument_key=body.instrument_key,
            expiry_date=body.expiry_date,
        )
        report = await asyncio.to_thread(
            capture_and_store, source, _snapshot_store(), body.underlying
        )
    except DataQualityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Capture run failed for %s: %s", body.underlying, exc)
        raise HTTPException(status_code=500, detail="Chain capture failed") from exc
    return dataclasses.asdict(report)


@router.get("/history/{underlying}")
async def capture_history(underlying: str, date: str | None = None) -> dict[str, Any]:
    """List stored snapshot paths for an underlying (optionally one UTC day)."""
    paths = _snapshot_store().list_snapshots(underlying, date)
    return {
        "underlying": underlying,
        "count": len(paths),
        "snapshots": [str(path) for path in paths],
    }


# ---------------------------------------------------------------------------
# Unattended capture scheduler (flagship phase 0 exit criterion)
# ---------------------------------------------------------------------------

# stop() wakes the scheduler loop immediately; this bound only guards against a
# capture thread that is mid-broker-request when the stop arrives.
SCHEDULER_STOP_JOIN_SECONDS = 5.0


class ScheduleStartRequest(BaseModel):
    """Parameters for one unattended capture schedule (single underlying/expiry)."""

    underlying: str = Field(description="Underlying name used for the snapshot store, e.g. NIFTY")
    instrument_key: str = Field(description="Upstox instrument key, e.g. 'NSE_INDEX|Nifty 50'")
    expiry_date: str = Field(description="Option expiry date, YYYY-MM-DD")
    interval_seconds: int | None = Field(
        default=None,
        gt=0,
        description="Capture cadence in seconds; defaults to settings.capture_interval_seconds",
    )


def _scheduler_status_payload(scheduler: CaptureScheduler | None) -> dict[str, Any]:
    """Serialize scheduler status + history; idle shape when nothing was started."""
    if scheduler is None:
        idle = SchedulerStatus(
            running=False,
            last_run_ts=None,
            last_error=None,
            n_captures=0,
            n_failures=0,
            next_eligible_ts=None,
        )
        return {**dataclasses.asdict(idle), "history": []}
    return {
        **dataclasses.asdict(scheduler.status()),
        "history": [{"ts": ts, "ok": ok, "detail": detail} for ts, ok, detail in scheduler.history],
    }


@router.post("/schedule/start")
async def start_capture_schedule(
    body: ScheduleStartRequest,
    request: Request,
    access_token: str = Depends(get_access_token),
) -> dict[str, Any]:
    """Start unattended interval capture for one underlying/expiry.

    Deliberately operator-initiated: nothing auto-starts at app startup,
    because a schedule needs a live Upstox token and a chosen expiry — both
    operator decisions. Returns 409 if a scheduler is already running; stop it
    via POST /capture/schedule/stop first.
    """
    task = getattr(request.app.state, "capture_scheduler_task", None)
    if task is not None and not task.done():
        raise HTTPException(
            status_code=409,
            detail="Capture scheduler already running; stop it via /capture/schedule/stop",
        )
    try:
        # Built once and reused: fetch_chain re-reads its clock per capture.
        # Also validates expiry_date up front instead of failing every cycle.
        source = UpstoxCaptureSource(
            access_token=access_token,
            instrument_key=body.instrument_key,
            expiry_date=body.expiry_date,
        )
    except DataQualityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    store = _snapshot_store()
    underlying = body.underlying

    pipeline = getattr(request.app.state, "live_pipeline", None)

    def capture_once() -> CaptureReport:
        report = capture_and_store(source, store, underlying)
        if pipeline is not None:
            chain = source.fetch_chain(underlying)
            pipeline.cache_chain(chain)
        return report

    interval = body.interval_seconds or settings.capture_interval_seconds
    scheduler = CaptureScheduler(
        capture_fn=capture_once,
        config=ScheduleConfig(interval_seconds=interval),
        on_capture=pipeline.on_capture if pipeline is not None else None,
    )
    request.app.state.capture_scheduler = scheduler
    request.app.state.capture_scheduler_task = asyncio.create_task(scheduler.run())
    logger.info(
        "Capture scheduler started: %s expiry %s every %ss", underlying, body.expiry_date, interval
    )
    return {
        "started": True,
        "underlying": underlying,
        "instrument_key": body.instrument_key,
        "expiry_date": body.expiry_date,
        "interval_seconds": interval,
    }


@router.post("/schedule/stop")
async def stop_capture_schedule(request: Request) -> dict[str, Any]:
    """Stop the running capture scheduler and wait for its loop to exit."""
    scheduler = getattr(request.app.state, "capture_scheduler", None)
    task = getattr(request.app.state, "capture_scheduler_task", None)
    if scheduler is None or task is None or task.done():
        raise HTTPException(status_code=409, detail="No capture scheduler is running")
    scheduler.stop()
    try:
        await asyncio.wait_for(task, timeout=SCHEDULER_STOP_JOIN_SECONDS)
    except TimeoutError:
        # wait_for has already cancelled the task; a capture thread may still
        # be mid-request, but the loop cannot fire again.
        logger.warning("Capture scheduler did not join within %ss", SCHEDULER_STOP_JOIN_SECONDS)
    except Exception as exc:
        # The loop itself ended with an error; surface it in status, not as a 500.
        logger.error("Capture scheduler task ended with error: %s", exc)
    request.app.state.capture_scheduler_task = None
    return {"stopped": True, **_scheduler_status_payload(scheduler)}


@router.get("/schedule/status")
async def capture_schedule_status(request: Request) -> dict[str, Any]:
    """Live scheduler status plus the most recent capture outcomes (max 100)."""
    scheduler = getattr(request.app.state, "capture_scheduler", None)
    return _scheduler_status_payload(scheduler)
