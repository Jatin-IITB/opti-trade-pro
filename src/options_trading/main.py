# src/options_trading/main.py
"""FastAPI main application entry point. Fixed authentication integration and error handling."""

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .api.routes.analytics import router as analytics_router
from .api.routes.auth import router as auth_router
from .api.routes.backtesting import router as backtesting_router
from .api.routes.capture import router as capture_router
from .api.routes.connectors import router as connectors_router
from .api.routes.dashboard import router as dashboard_router
from .api.routes.market_data import router as market_data_router
from .config.settings import get_settings
from .market_data.manager import MarketDataManager
from .services.auth_service import AuthService
from .services.dashboard_service import DashboardService
from .services.live_pipeline import LivePipelineConfig, LivePipelineService
from .services.market_data_service import MarketDataService
from .services.strategy_service import StrategyService
from .services.websocket_manager import WebSocketManager
from .utils.cache import AsyncCache
from .utils.exceptions import OptionsTradinError  # keeping your class name as provided

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

websocket_manager = WebSocketManager()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager with proper error handling."""
    # Startup
    logger.info("🚀 Starting Options Trading Platform v2.0")
    settings = get_settings()
    app.state.settings = settings

    logger.info("🔌 Initializing WebSocket manager...")
    app.state.websocket_manager = websocket_manager

    logger.info("📡 Initializing live pipeline service...")
    app.state.live_pipeline = LivePipelineService(
        ws_manager=websocket_manager,
        config=LivePipelineConfig(),
    )

    logger.info(f"Environment: {settings.environment}")
    logger.info(f"Debug mode: {settings.debug}")

    # Start periodic tasks
    logger.info("⏰ Starting background tasks...")
    app.state.cache = AsyncCache(ttl=120, max_size=500)

    # Initialize AuthService
    auth_service = AuthService()
    app.state.auth_service = auth_service
    logger.info("🔐 AuthService initialized")

    # Try to initialize core services with authentication
    try:
        async with auth_service as auth:
            access_token = await auth.get_valid_access_token()
        logger.info("✅ Valid access token obtained")

        # Initialize MarketDataManager with authenticated token
        market_data_manager = MarketDataManager(access_token=access_token)
        app.state.market_data_manager = market_data_manager
        logger.info("📈 MarketDataManager initialized with access token")

        # Initialize dependent services
        app.state.market_data_service = MarketDataService(market_data_manager=market_data_manager)
        logger.info("📊 MarketDataService initialized")

        app.state.dashboard_service = DashboardService(market_data_manager=market_data_manager)
        logger.info("📊 DashboardService initialized")

        app.state.strategy_service = StrategyService(
            market_data_service=app.state.market_data_service
        )
        logger.info("📈 StrategyService initialized")

    except Exception as e:
        logger.warning(f"⚠️ Core services initialization failed: {e}")
        logger.info("🔄 Services will initialize after user authentication")
        # Set services to None - they'll be initialized after auth
        app.state.market_data_manager = None
        app.state.market_data_service = None
        app.state.dashboard_service = None
        app.state.strategy_service = None

    yield

    # Shutdown
    logger.info("🛑 Shutting down background tasks...")
    if hasattr(app.state, "cache"):
        logger.info("💾 Cache cleared")
    logger.info("👋 Shutting down Options Trading Platform")


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    settings = get_settings()
    app = FastAPI(
        title="Options Trading Platform",
        description="Modern options trading platform with Black-Scholes calculations and gamma scalping",
        version="2.1.0",
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        lifespan=lifespan,
    )

    # Sessions first. A guessable session secret in production is an account
    # takeover vector, so there is no fallback outside debug mode.
    secret_key = settings.secret_key or os.getenv("SECRET_KEY", "")
    if secret_key in ("", "changeme", "dev-secret-key") and not settings.debug:
        raise RuntimeError(
            "SECRET_KEY must be set to a strong value outside debug mode (see .env.example)"
        )
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret_key,
        session_cookie="session",
        max_age=settings.oauth_timeout_seconds,
        same_site="lax",
        https_only=not settings.debug,
    )

    # Security middleware (production)
    if not settings.debug:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])

    # CORS: wildcard origins with credentials enabled is a CSRF hazard, so the
    # list is explicit; extend via deployment config, not back to "*".
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://localhost:3000",
            "http://localhost:5173",
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
    )

    # Static files and templates
    here = Path(__file__).parent
    static_dir = here / "static"
    templates_dir = here / "templates"

    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    templates = Jinja2Templates(directory=templates_dir) if templates_dir.exists() else None

    # Routers
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(dashboard_router, prefix="/api/v1")
    app.include_router(market_data_router, prefix="/api/v1")
    app.include_router(backtesting_router, prefix="/api/v1")
    app.include_router(analytics_router, prefix="/api/v1")
    app.include_router(capture_router, prefix="/api/v1")
    app.include_router(connectors_router, prefix="/api/v1")

    # Exception handlers
    @app.exception_handler(OptionsTradinError)
    async def options_trading_exception_handler(request: Request, exc: OptionsTradinError):
        logger.error(f"Platform error: {exc}")
        return JSONResponse(
            status_code=getattr(exc, "status_code", 400),
            content={
                "error": getattr(exc, "error_code", "PLATFORM_ERROR"),
                "message": exc.message if hasattr(exc, "message") else str(exc),
                "details": getattr(exc, "details", {}),
            },
        )

    @app.exception_handler(500)
    async def internal_server_error_handler(request: Request, exc: Exception):
        logger.error(f"Internal server error: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "error": "INTERNAL_SERVER_ERROR",
                "message": "An internal error occurred" if not settings.debug else str(exc),
            },
        )

    # Health
    @app.get("/health", tags=["Root"])
    async def health_check(request: Request):
        return {
            "status": "healthy",
            "version": "2.1.0",
            "environment": settings.environment,
            "services": {
                "auth_service": "initialized"
                if hasattr(request.app.state, "auth_service")
                else "failed",
                "market_data_manager": "initialized"
                if getattr(request.app.state, "market_data_manager", None)
                else "not_initialized",
                "websocket_manager": "initialized"
                if hasattr(request.app.state, "websocket_manager")
                else "failed",
                "cache": "initialized" if hasattr(request.app.state, "cache") else "failed",
            },
        }

    @app.get("/", tags=["Root"])
    async def root():
        return {
            "message": "Options Trading Platform v2.1",
            "docs": "/docs",
            "health": "/health",
            "auth": "/api/v1/auth/login",
            "dashboard": "/dashboard",
        }

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard_page(request: Request):
        """Dashboard page with proper authentication checking."""
        try:
            authenticated = False
            user_id = None
            try:
                if request.session.get("authenticated") is True:
                    user_id = request.session.get("authenticated_user_id")
                    if user_id:
                        authenticated = True
            except Exception:
                authenticated = False

            if not authenticated:
                auth_service = getattr(request.app.state, "auth_service", None)
                if auth_service is None:
                    auth_service = AuthService()
                    request.app.state.auth_service = auth_service
                try:
                    stored_users = await auth_service.storage.list_stored_users()
                    if stored_users:
                        user_id = stored_users[0]
                        try:
                            request.session["authenticated"] = True
                            request.session["authenticated_user_id"] = user_id
                        except Exception:
                            pass
                        authenticated = True
                except Exception:
                    authenticated = False

            if not authenticated:
                logger.info("User not authenticated, redirecting to login")
                return RedirectResponse(url="/api/v1/auth/login")

            if not getattr(request.app.state, "market_data_manager", None):
                try:
                    async with request.app.state.auth_service as auth:
                        access_token = await auth.get_valid_access_token(user_id or "default")
                    from .utils.app_init import initialize_app_services

                    await initialize_app_services(
                        request.app, access_token=access_token, user_id=user_id
                    )
                except Exception:
                    return RedirectResponse(url="/api/v1/auth/login")

            # If we reach here, user is authenticated and services are initialized
            user_context = {
                "user_id": user_id or "default",
                "user_name": "Trader",
                "email": "N/A",
                "authenticated": True,
            }

            try:
                async with request.app.state.auth_service as auth:
                    token = await auth.get_valid_access_token(user_id or "default")
                    profile = await auth.get_user_profile(token)
                user_context.update(
                    {
                        "user_id": profile.user_id,
                        "user_name": profile.user_name or "Trader",
                        "email": profile.email or "N/A",
                    }
                )
            except Exception:
                pass

            if templates:
                return templates.TemplateResponse(
                    "index.html",
                    {
                        "request": request,
                        **user_context,
                        "api_base": "/api/v1",
                        "websocket_url": "ws://localhost:8000/api/v1/dashboard/ws",
                    },
                )
            else:
                return HTMLResponse(
                    content=f"""
                    <html><body>
                    <h1>Welcome to your trading dashboard!</h1>
                    <p>Dashboard template not found. Using fallback interface.</p>
                    <p>Place your templates in:<br/><code>{(Path(__file__).parent / "templates")}</code></p>
                    </body></html>
                    """,
                    status_code=200,
                )
        except Exception as e:
            logger.error(f"Dashboard rendering error: {e}", exc_info=True)
            return HTMLResponse("Internal error", status_code=500)

    return app


app = create_app()
if __name__ == "__main__":
    uvicorn.run("options_trading.main:app", host="0.0.0.0", port=8000, factory=True, reload=True)
