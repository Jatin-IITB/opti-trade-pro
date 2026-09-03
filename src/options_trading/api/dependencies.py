# src/options_trading/api/dependencies.py
from __future__ import annotations

import logging

from fastapi import HTTPException, Request, status

from ..market_data.manager import MarketDataManager
from ..services.auth_service import AuthService
from ..services.dashboard_service import DashboardService
from ..services.market_data_service import MarketDataService
from ..utils.app_init import initialize_app_services
from ..utils.cache import AsyncCache

logger = logging.getLogger(__name__)

_spot_cache: AsyncCache | None = None


def get_spot_cache() -> AsyncCache:
    global _spot_cache
    if _spot_cache is None:
        _spot_cache = AsyncCache(max_cache_size=20)
        logger.info("Created new SpotDataCache instance")
    return _spot_cache


def get_market_data_manager(request: Request) -> MarketDataManager:
    if not request.app.state.market_data_manager:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Market data manager not initialized",
        )
    return request.app.state.market_data_manager


async def get_dashboard_service(request: Request) -> DashboardService:
    svc = getattr(request.app.state, "dashboard_service", None)
    if svc:
        return svc
    init_ok = await initialize_app_services(request.app)
    svc = getattr(request.app.state, "dashboard_service", None)
    if svc:
        return svc
    logger.error("DashboardService not available and initialization failed")
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Dashboard service not initialized"
    )


def get_market_data_service(request: Request) -> MarketDataService:
    if not request.app.state.market_data_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Market data service not initialized",
        )
    return request.app.state.market_data_service


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service
