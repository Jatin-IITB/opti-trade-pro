# src/options_trading/utils/app_init.py
import asyncio
import logging

from ..services.auth_service import AuthService
from ..utils.exceptions import AuthError

logger = logging.getLogger(__name__)

# NOTE: imports of concrete service classes are done inside the function
# so this module is safe to import at app startup without pulling heavy deps.


async def initialize_app_services(
    app, user_id: str | None = None, access_token: str | None = None
) -> bool:
    """
    Initialize core app services and attach them to app.state.
    If access_token is not supplied, will try to obtain one via AuthService.get_valid_access_token(user_id).
    Returns True if services are ready, False otherwise.
    """
    # Ensure a per-app lock
    if not hasattr(app.state, "service_init_lock"):
        app.state.service_init_lock = asyncio.Lock()

    async with app.state.service_init_lock:
        # If already initialized, return True
        if getattr(app.state, "dashboard_service", None):
            logger.debug("App services already initialized; skipping re-init")
            return True

        # Resolve access token if not provided
        if not access_token:
            try:
                async with AuthService() as auth:
                    token = await auth.get_valid_access_token(user_id or "default")
                    access_token = token
                    logger.debug(
                        "Resolved access_token via AuthService for user '%s'", user_id or "default"
                    )
            except AuthError as e:
                logger.warning(
                    "Could not resolve access token for user '%s': %s", user_id or "default", e
                )
                return False
            except Exception as e:
                logger.exception("Unexpected error resolving access token: %s", e)
                return False

        # At this point we have an access_token (string)
        try:
            # import heavy modules lazily
            from ..market_data.manager import MarketDataManager
            from ..services.dashboard_service import DashboardService
            from ..services.market_data_service import MarketDataService

            # StrategyService may not exist; import if present
            try:
                from ..services.strategy_service import StrategyService
            except Exception:
                StrategyService = None

            # create manager + services and attach to app.state
            market_data_manager = MarketDataManager(access_token=access_token)
            app.state.market_data_manager = market_data_manager

            app.state.market_data_service = MarketDataService(
                market_data_manager=market_data_manager
            )

            # Late-bound lookups: the portfolio sync and live pipeline may be
            # created after this runs, and both can be replaced on re-login.
            def _current_book():
                svc = getattr(app.state, "portfolio_sync", None)
                return svc.get_book_context() if svc is not None else None

            def _current_spot():
                pipeline = getattr(app.state, "live_pipeline", None)
                return pipeline.get_latest_spot() if pipeline is not None else None

            app.state.dashboard_service = DashboardService(
                market_data_manager=market_data_manager,
                book_fn=_current_book,
                spot_fn=_current_spot,
            )

            if StrategyService:
                app.state.strategy_service = StrategyService(
                    market_data_service=app.state.market_data_service
                )
            else:
                app.state.strategy_service = None

            # Log only short masked token for safety
            masked = access_token[:6] + "..." if isinstance(access_token, str) else "<not-string>"
            logger.info("Application services initialized; token prefix=%s", masked)
            return True

        except Exception as e:
            logger.exception("Failed to initialize application services: %s", e)
            # Make sure partial state isn't left behind
            for key in (
                "market_data_manager",
                "market_data_service",
                "dashboard_service",
                "strategy_service",
            ):
                if hasattr(app.state, key):
                    try:
                        delattr(app.state, key)
                    except Exception:
                        pass
            return False
