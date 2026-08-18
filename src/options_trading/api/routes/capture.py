# src/options_trading/api/routes/capture.py
"""Chain-capture endpoints: live Upstox chains -> quote filters -> Parquet snapshots."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from optitrade.data import SnapshotStore

from ...config.settings import settings
from ...services.auth_service import AuthService
from ...services.capture_service import UpstoxCaptureSource, capture_and_store
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
