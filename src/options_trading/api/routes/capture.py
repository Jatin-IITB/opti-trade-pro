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
from ...services.capture_control import (
    CaptureTarget,
    is_schedule_running,
    resolve_autostart_target,
    start_schedule,
)
from ...services.capture_scheduler import CaptureScheduler, SchedulerStatus
from ...services.capture_service import UpstoxCaptureSource, capture_and_store
from ...services.token_provider import get_token_provider
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
    """Start unattended interval capture for one explicit underlying/expiry.

    The app also auto-starts a schedule on the nearest expiry at boot (see
    ``settings.capture_autostart``); this endpoint is for overriding that
    choice. Returns 409 if a scheduler is already running; stop it via
    POST /capture/schedule/stop first.
    """
    if is_schedule_running(request.app):
        raise HTTPException(
            status_code=409,
            detail="Capture scheduler already running; stop it via /capture/schedule/stop",
        )

    target = CaptureTarget(
        underlying=body.underlying,
        instrument_key=body.instrument_key,
        expiry_date=body.expiry_date,
        interval_seconds=body.interval_seconds or settings.capture_interval_seconds,
    )
    try:
        start_schedule(request.app, target, get_token_provider(request.app))
    except DataQualityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "started": True,
        "underlying": target.underlying,
        "instrument_key": target.instrument_key,
        "expiry_date": target.expiry_date,
        "interval_seconds": target.interval_seconds,
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


@router.post("/schedule/auto")
async def start_capture_schedule_auto(
    request: Request,
    access_token: str = Depends(get_access_token),
) -> dict[str, Any]:
    """Start capture on the nearest tradable expiry, resolving the target itself.

    The UI-facing counterpart to ``/schedule/start``: the caller does not need
    to know the instrument key or expiry ladder.
    """
    if is_schedule_running(request.app):
        raise HTTPException(
            status_code=409,
            detail="Capture scheduler already running; stop it via /capture/schedule/stop",
        )
    try:
        target = await resolve_autostart_target(access_token)
    except DataQualityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    start_schedule(request.app, target, get_token_provider(request.app))
    return {
        "started": True,
        "underlying": target.underlying,
        "instrument_key": target.instrument_key,
        "expiry_date": target.expiry_date,
        "interval_seconds": target.interval_seconds,
    }


@router.get("/schedule/status")
async def capture_schedule_status(request: Request) -> dict[str, Any]:
    """Live scheduler status plus the most recent capture outcomes (max 100)."""
    scheduler = getattr(request.app.state, "capture_scheduler", None)
    target = getattr(request.app.state, "capture_target", None)
    return {
        **_scheduler_status_payload(scheduler),
        "target": dataclasses.asdict(target) if target is not None else None,
    }
