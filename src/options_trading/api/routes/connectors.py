"""Broker connector configuration routes — schema, save, test, delete."""

import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...services.connector_store import BROKER_SCHEMAS, ConnectorStore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/connectors", tags=["connectors"])

_store = ConnectorStore()


class SaveConfigRequest(BaseModel):
    values: dict[str, str]


class TestResult(BaseModel):
    success: bool
    message: str


class ConnectorInfo(BaseModel):
    broker_id: str
    configured: bool
    config: dict[str, Any] | None = None


@router.get("/schema/{broker_id}")
async def get_broker_schema(broker_id: str) -> dict[str, Any]:
    """Return field schema for a broker's configuration form."""
    schema = BROKER_SCHEMAS.get(broker_id)
    if not schema:
        raise HTTPException(status_code=404, detail=f"Unknown broker: {broker_id}")
    return {"broker_id": broker_id, "fields": schema}


@router.get("/")
async def list_connectors() -> list[ConnectorInfo]:
    """List all broker connectors with their configuration status."""
    result = []
    for broker_id in BROKER_SCHEMAS:
        configured = await _store.is_configured(broker_id)
        config = await _store.get_config_redacted(broker_id) if configured else None
        result.append(ConnectorInfo(broker_id=broker_id, configured=configured, config=config))
    return result


@router.get("/{broker_id}")
async def get_connector(broker_id: str) -> ConnectorInfo:
    """Get a single connector's configuration (secrets redacted)."""
    if broker_id not in BROKER_SCHEMAS:
        raise HTTPException(status_code=404, detail=f"Unknown broker: {broker_id}")
    configured = await _store.is_configured(broker_id)
    config = await _store.get_config_redacted(broker_id) if configured else None
    return ConnectorInfo(broker_id=broker_id, configured=configured, config=config)


@router.put("/{broker_id}")
async def save_connector(broker_id: str, body: SaveConfigRequest) -> ConnectorInfo:
    """Save broker credentials (encrypted at rest)."""
    if broker_id not in BROKER_SCHEMAS:
        raise HTTPException(status_code=404, detail=f"Unknown broker: {broker_id}")

    schema = BROKER_SCHEMAS[broker_id]
    required = {f["field"] for f in schema if f.get("required")}
    missing = required - set(body.values.keys())

    if missing:
        existing = await _store.get_config(broker_id)
        if existing:
            still_missing = missing - set(existing.keys())
        else:
            still_missing = missing
        if still_missing:
            raise HTTPException(
                status_code=422,
                detail=f"Missing required fields: {', '.join(sorted(still_missing))}",
            )

    await _store.save_config(broker_id, body.values)
    config = await _store.get_config_redacted(broker_id)
    return ConnectorInfo(broker_id=broker_id, configured=True, config=config)


@router.delete("/{broker_id}")
async def delete_connector(broker_id: str) -> dict[str, str]:
    """Delete broker credentials."""
    if broker_id not in BROKER_SCHEMAS:
        raise HTTPException(status_code=404, detail=f"Unknown broker: {broker_id}")
    deleted = await _store.delete_config(broker_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="No config to delete")
    return {"status": "deleted", "broker_id": broker_id}


@router.post("/{broker_id}/test")
async def test_connector(broker_id: str) -> TestResult:
    """Test broker credentials by making a lightweight API call."""
    config = await _store.get_config(broker_id)
    if not config:
        return TestResult(success=False, message="Not configured — save credentials first.")

    if broker_id == "upstox":
        return await _test_upstox(config)

    return TestResult(success=False, message=f"{broker_id} testing not implemented yet.")


async def _test_upstox(config: dict[str, str]) -> TestResult:
    """Validate Upstox credentials by hitting the token endpoint with a dummy code."""
    api_key = config.get("api_key", "")
    api_secret = config.get("api_secret", "")
    redirect_uri = config.get("redirect_uri", "")

    if not api_key or not api_secret:
        return TestResult(success=False, message="API key or secret is empty.")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.upstox.com/v2/login/authorization/token",
                data={
                    "code": "test_validation_only",
                    "client_id": api_key,
                    "client_secret": api_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            body = resp.json()

            if resp.status_code == 400:
                err = body.get("errors", [{}])[0] if body.get("errors") else body
                code = err.get("errorCode", err.get("error_code", ""))
                if code == "UDAPI100068":
                    return TestResult(
                        success=False,
                        message="Invalid client_id or redirect_uri. Check your Upstox app settings.",
                    )
                if code in ("UDAPI100069", "UDAPI1000"):
                    return TestResult(
                        success=True,
                        message="Credentials valid — API key and redirect URI accepted by Upstox.",
                    )
                return TestResult(success=False, message=f"Upstox error: {err.get('message', code)}")

            return TestResult(success=True, message="Credentials accepted by Upstox.")

    except httpx.TimeoutException:
        return TestResult(success=False, message="Upstox API timed out — try again.")
    except Exception as e:
        return TestResult(success=False, message=f"Connection error: {e}")
