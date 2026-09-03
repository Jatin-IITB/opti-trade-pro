# src/options_trading/api/routes/dashboard.py
"""
Production-grade dashboard routes for options trading platform.
"""

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from starlette.status import HTTP_500_INTERNAL_SERVER_ERROR

from ...models.dashboard import (
    DashboardConfig,
    MarketDataStatus,
    PositionSummary,
    RiskMetrics,
    StrategyPerformance,
    SystemStatus,
)
from ...services.dashboard_service import DashboardService
from ...services.market_data_service import MarketDataService
from ...services.websocket_manager import WebSocketManager
from ...utils.auth_dependencies import get_current_user
from ..dependencies import get_dashboard_service, get_market_data_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _ws_manager(app: Any) -> WebSocketManager:
    """Resolve the application-wide WebSocketManager.

    There must be exactly one instance per app: browser clients register on
    it here, and ``LivePipelineService`` / ``PortfolioSyncService`` broadcast
    on it. A second instance would accept connections that no broadcaster can
    ever reach, so this reads from ``app.state`` rather than constructing one.
    """
    manager = getattr(app.state, "websocket_manager", None)
    if manager is None:
        manager = WebSocketManager()
        app.state.websocket_manager = manager
        logger.warning("websocket_manager missing from app.state; created one lazily")
    return manager


@router.get("/status", response_model=SystemStatus)
async def get_system_status(
    current_user: dict = Depends(get_current_user),
    dashboard_service: DashboardService = Depends(get_dashboard_service),
) -> SystemStatus:
    try:
        user_id = current_user.get("user_id")
        status = await dashboard_service.get_system_status(user_id=user_id)
        logger.info(f"System status retrieved for user {user_id}: {status}")
        return status
    except Exception as e:
        logger.error(f"Failed to get system status: {e}", exc_info=True)
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve system status"
        )


@router.get("/market-data/status", response_model=MarketDataStatus)
async def get_market_data_status(
    current_user: dict = Depends(get_current_user),
    market_service: MarketDataService = Depends(get_market_data_service),
) -> MarketDataStatus:
    try:
        return await market_service.get_data_status()
    except Exception as e:
        logger.error(f"Failed to get market data status: {e}")
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail="Market data status unavailable"
        )


@router.get("/strategies/performance", response_model=list[StrategyPerformance])
async def get_strategy_performance(
    limit: int = 10,
    active_only: bool = True,
    current_user: dict = Depends(get_current_user),
    dashboard_service: DashboardService = Depends(get_dashboard_service),
) -> list[StrategyPerformance]:
    try:
        strategies = await dashboard_service.get_strategy_performance(
            limit=limit, active_only=active_only
        )
        return strategies
    except Exception as e:
        logger.error(f"Failed to get strategy performance: {e}")
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Strategy performance data unavailable",
        )


@router.get("/positions/summary", response_model=PositionSummary)
async def get_positions_summary(
    symbol: str | None = None,
    expiry_date: str | None = None,
    current_user: dict = Depends(get_current_user),
    dashboard_service: DashboardService = Depends(get_dashboard_service),
) -> PositionSummary:
    try:
        summary = await dashboard_service.get_positions_summary(
            symbol=symbol, expiry_date=expiry_date
        )
        return summary
    except Exception as e:
        logger.error(f"Failed to get positions summary: {e}")
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail="Positions summary unavailable"
        )


@router.get("/risk/metrics", response_model=RiskMetrics)
async def get_risk_metrics(
    current_user: dict = Depends(get_current_user),
    dashboard_service: DashboardService = Depends(get_dashboard_service),
) -> RiskMetrics:
    try:
        metrics = await dashboard_service.calculate_risk_metrics()
        return metrics
    except Exception as e:
        logger.error(f"Failed to calculate risk metrics: {e}")
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail="Risk metrics calculation failed"
        )


@router.websocket("/ws/{client_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    client_id: str,
):
    websocket_manager = _ws_manager(websocket.app)
    await websocket_manager.connect(websocket, client_id)
    try:
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "subscribe":
                symbols = message.get("symbols", [])
                await websocket_manager.subscribe_client(client_id, symbols)
                await websocket.send_json(
                    {
                        "type": "subscription_confirmed",
                        "symbols": symbols,
                        "timestamp": datetime.now().isoformat(),
                    }
                )
            elif message.get("type") == "unsubscribe":
                symbols = message.get("symbols", [])
                await websocket_manager.unsubscribe_client(client_id, symbols)
                await websocket.send_json(
                    {
                        "type": "unsubscription_confirmed",
                        "symbols": symbols,
                        "timestamp": datetime.now().isoformat(),
                    }
                )
            elif message.get("type") == "request_snapshot":
                pipeline = getattr(websocket.app.state, "live_pipeline", None)
                if pipeline is not None:
                    snapshot = pipeline.get_latest_snapshot()
                    if snapshot is not None:
                        await websocket.send_json(
                            {
                                "type": "dashboard_update",
                                # Same builder as the push path, so a reconnecting
                                # client cannot receive a payload missing panels.
                                "data": await pipeline.build_wire_dict(snapshot),
                                "timestamp": datetime.now().isoformat(),
                            }
                        )
            elif message.get("type") == "ping":
                await websocket.send_json({"type": "pong", "timestamp": datetime.now().isoformat()})
    except WebSocketDisconnect:
        logger.info(f"Client {client_id} disconnected from WebSocket")
        await websocket_manager.disconnect(websocket, client_id)
    except Exception as e:
        logger.error(f"WebSocket error for client {client_id}: {e}")
        await websocket_manager.disconnect(websocket, client_id)


@router.get("/config", response_model=DashboardConfig)
async def get_dashboard_config(
    current_user: dict = Depends(get_current_user),
    dashboard_service: DashboardService = Depends(get_dashboard_service),
) -> DashboardConfig:
    try:
        config = await dashboard_service.get_dashboard_config(user_id=current_user.get("user_id"))
        return config
    except Exception as e:
        logger.error(f"Failed to get dashboard config: {e}")
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail="Dashboard configuration unavailable"
        )


@router.post("/config")
async def update_dashboard_config(
    config: DashboardConfig,
    current_user: dict = Depends(get_current_user),
    dashboard_service: DashboardService = Depends(get_dashboard_service),
) -> dict[str, str]:
    try:
        await dashboard_service.update_dashboard_config(
            user_id=current_user.get("user_id"), config=config
        )
        return {"message": "Dashboard configuration updated successfully"}
    except Exception as e:
        logger.error(f"Failed to update dashboard config: {e}")
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Dashboard configuration update failed",
        )


@router.get("/live/snapshot")
async def get_live_snapshot(request: Request) -> Any:
    """Latest computed analytics snapshot for REST polling fallback."""
    pipeline = getattr(request.app.state, "live_pipeline", None)
    if pipeline is None:
        raise HTTPException(status_code=503, detail="Live pipeline not initialized")
    snapshot = pipeline.get_latest_snapshot()
    if snapshot is None:
        return JSONResponse(status_code=204, content=None)
    return await pipeline.build_wire_dict(snapshot)


@router.get("/health")
async def dashboard_health_check(request: Request) -> dict[str, Any]:
    try:
        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "active_connections": _ws_manager(request.app).connection_count(),
            "version": "2.0.0",
        }
    except Exception as e:
        logger.error(f"Dashboard health check failed: {e}")
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail="Dashboard health check failed"
        )
