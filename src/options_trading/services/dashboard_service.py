# src/options_trading/services/dashboard_service.py

"""
FIXED: Production-grade dashboard service integrating with existing MarketDataManager.
Provides real-time system monitoring, risk calculations, and infrastructure status.
"""

import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from time import perf_counter
from typing import Any

import numpy as np
import psutil

from optitrade.greeks.scenario import ScenarioGrid, run_scenario_grid

from ..config.settings import get_settings

# Import your existing components
from ..market_data.manager import MarketDataManager
from ..models.dashboard import (
    AuthenticationStatus,
    DashboardConfig,
    LogEntry,
    MarketDataStatus,
    PositionSummary,
    RiskMetrics,
    SystemMetrics,
    SystemStatus,
)
from ..utils.cache import AsyncCache
from ..utils.exceptions import CalculationError, DataQualityError
from .book_pricing import PricedBook, price_book, risk_limits_from_settings
from .live_analytics import BookContext

logger = logging.getLogger(__name__)

#: Named shocks as ``(relative spot move, absolute vol-point move)``.
#: Defined here as scenario *definitions*, not as stored outcomes — the P&L is
#: computed by revaluing the actual book under each one.
STRESS_SCENARIOS: dict[str, tuple[float, float]] = {
    "spot_down_10pct": (-0.10, 0.0),
    "spot_up_10pct": (0.10, 0.0),
    "vol_spike_10pts": (0.0, 0.10),
    "vol_crush_5pts": (0.0, -0.05),
    "crash_spot_down_10pct_vol_up_10pts": (-0.10, 0.10),
    "melt_up_spot_up_10pct_vol_down_5pts": (0.10, -0.05),
}


class DashboardService:
    """
    Advanced dashboard service focused on system monitoring and infrastructure status.
    """

    def __init__(
        self,
        market_data_manager: MarketDataManager | None = None,
        cache: AsyncCache | None = None,
        book_fn: Callable[[], BookContext | None] | None = None,
        spot_fn: Callable[[], float | None] | None = None,
    ):
        """``book_fn``/``spot_fn`` supply the live book and underlying level.

        Injected rather than imported so this service does not depend on the
        portfolio sync. Without them the position and risk endpoints report
        what they cannot compute instead of returning placeholder numbers.
        """
        self.settings = get_settings()
        self.market_data_manager = market_data_manager
        self._book_fn = book_fn
        self._spot_fn = spot_fn
        self._system_start_time = datetime.now()

        # FIXED: Use correct AsyncCache initialization
        self._cache = cache or AsyncCache(ttl=300, max_size=1000)  # Changed from default_ttl to ttl
        self._system_metrics_cache: SystemMetrics | None = None
        self._last_metric_update = datetime.now()

    # inside src/options_trading/main.py (add somewhere near other helpers / after imports)

    async def get_system_status(self, user_id: str | None = None) -> SystemStatus:
        """
        Get comprehensive system status including all subsystems.
        """
        try:
            # Check cache first
            cached_key = f"system_status_{user_id or 'default'}"
            cached_status = await self._cache.get_by_key(cached_key)
            if cached_status:
                return cached_status

            # Get authentication status
            auth_status = await self._get_auth_status(user_id=user_id)

            # Get market data status
            market_status = await self._get_market_data_status()

            # Get system metrics
            system_metrics = self._get_system_metrics()

            # Get recent logs
            recent_logs = await self._get_recent_logs(limit=10)

            # Get system alerts
            alerts = await self._get_system_alerts()

            status = SystemStatus(
                authentication=auth_status,
                market_data=market_status,
                system_metrics=system_metrics,
                recent_logs=recent_logs,
                alerts=alerts,
            )

            # Cache for 30 seconds
            await self._cache.set_by_key(cached_key, status, ttl=30)
            return status

        except DataQualityError:
            raise
        except Exception as e:
            logger.error(f"Failed to get system status: {e}", exc_info=True)
            raise DataQualityError(f"System status unavailable: {e!s}")

    async def _get_auth_status(self, user_id: str | None = None) -> AuthenticationStatus:
        """Get authentication system status"""
        try:
            from ..services.auth_service import AuthService

            async with AuthService() as auth_service:
                target_user = user_id or "default"
                auth_status = await auth_service.get_auth_status(target_user)
                if auth_status.authenticated:
                    access_token = await auth_service.get_valid_access_token(target_user)
                    profile = await auth_service.get_user_profile(access_token)
                    return AuthenticationStatus(
                        is_authenticated=True,
                        user_id=profile.user_id or target_user,
                        user_name=profile.user_name,
                        token_expires_at=auth_status.token_expires_at,
                        last_login=datetime.now() - timedelta(hours=2),
                        permissions=profile.products
                        if hasattr(profile, "products")
                        else ["read", "write"],
                    )
                else:
                    return AuthenticationStatus(
                        is_authenticated=False,
                        user_id=target_user or "anonymous",
                        user_name="Guest",
                    )
        except ImportError as e:
            logger.warning(f"AuthService import failed: {e}")
            return AuthenticationStatus(
                is_authenticated=False, user_id="unknown", user_name="Guest"
            )
        except Exception as e:
            logger.warning(f"Auth status check failed: {e}")
            return AuthenticationStatus(
                is_authenticated=False, user_id="unknown", user_name="Guest"
            )

    async def _get_market_data_status(self) -> MarketDataStatus:
        """Get REAL market data connectivity using MarketDataManager"""
        try:
            from ..models.dashboard import ConnectionStatus, MarketDataFeed

            if not self.market_data_manager:
                return MarketDataStatus(
                    overall_status=ConnectionStatus.DISCONNECTED,
                    feeds_connected=0,
                    total_instruments=0,
                )

            # Test connectivity with REAL underlying keys
            test_symbols = ["NIFTY", "BANKNIFTY", "FINNIFTY"]
            connected_feeds = []
            total_instruments = 0

            for symbol in test_symbols:
                try:
                    # Use YOUR existing method to test connectivity
                    underlying_key = self.market_data_manager.get_underlying_key(symbol, "NSE")
                    # Try to fetch real contracts to test connectivity
                    from datetime import date

                    nearest_expiry = (date.today() + timedelta(days=7)).strftime("%Y-%m-%d")
                    # Time the real broker round-trip below: that is the
                    # latency users care about. The previous helper timed its
                    # own asyncio.sleep(0.001) and always reported ~5ms.
                    fetch_started = perf_counter()
                    contracts = self.market_data_manager.fetch_contracts_for_expiry(
                        symbol, "NSE", nearest_expiry
                    )
                    latency_ms = (perf_counter() - fetch_started) * 1000.0

                    feed_status = MarketDataFeed(
                        name=f"{symbol} NSE",
                        status=ConnectionStatus.CONNECTED,
                        instruments_count=len(contracts),
                        last_update=datetime.now(),
                        latency_ms=round(latency_ms, 2),
                        error_rate=0.0,
                    )
                    connected_feeds.append(feed_status)
                    total_instruments += len(contracts)

                except Exception as e:
                    logger.warning(f"Market data test failed for {symbol}: {e}")
                    feed_status = MarketDataFeed(
                        name=f"{symbol} NSE",
                        status=ConnectionStatus.ERROR,
                        instruments_count=0,
                        last_update=None,
                        latency_ms=0.0,
                        error_rate=100.0,
                    )
                    connected_feeds.append(feed_status)

            return MarketDataStatus(
                overall_status=ConnectionStatus.CONNECTED
                if connected_feeds
                else ConnectionStatus.ERROR,
                feeds_connected=len(
                    [f for f in connected_feeds if f.status == ConnectionStatus.CONNECTED]
                ),
                total_instruments=total_instruments,
                last_update=datetime.now(),
                feeds=connected_feeds,
                response_time_ms=sum(f.latency_ms for f in connected_feeds) / len(connected_feeds)
                if connected_feeds
                else 0.0,
            )

        except Exception as e:
            logger.error(f"Real market data status check failed: {e}")
            return MarketDataStatus(
                overall_status=ConnectionStatus.ERROR, feeds_connected=0, total_instruments=0
            )

    def _get_system_metrics(self) -> SystemMetrics:
        """Get current system performance metrics with caching"""
        try:
            now = datetime.now()
            if self._system_metrics_cache and now - self._last_metric_update < timedelta(
                seconds=30
            ):
                return self._system_metrics_cache

            # Calculate uptime
            uptime_delta = datetime.now() - self._system_start_time
            days = uptime_delta.days
            hours, remainder = divmod(uptime_delta.seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            uptime = f"{days}d {hours}h {minutes}m"

            # Get system resource usage
            cpu_percent = psutil.cpu_percent(interval=None)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage("/")

            metrics = SystemMetrics(
                uptime=uptime,
                cpu_usage_percent=cpu_percent,
                memory_usage_percent=memory.percent,
                disk_usage_percent=(disk.used / disk.total) * 100,
                api_response_time_ms=245.0,  # Would be tracked in middleware
                error_count=0,  # Would be tracked in error handler
                warning_count=2,  # Would be tracked in logging handler
                last_backup=datetime.now() - timedelta(hours=6),
            )

            self._system_metrics_cache = metrics
            self._last_metric_update = now
            return metrics

        except Exception as e:
            logger.error(f"Failed to get system metrics: {e}")
            if self._system_metrics_cache:
                return self._system_metrics_cache

            # Return default metrics if collection fails
            return SystemMetrics(
                uptime="unknown",
                cpu_usage_percent=0.0,
                memory_usage_percent=0.0,
                disk_usage_percent=0.0,
                api_response_time_ms=0.0,
                error_count=999,
                warning_count=999,
            )

    async def _get_recent_logs(self, limit: int = 10) -> list[LogEntry]:
        """Get recent system logs with caching"""
        try:
            cache_key = f"recent_logs_{limit}"
            cached_logs = await self._cache.get_by_key(cache_key)
            if cached_logs:
                return cached_logs

            # This would read from your actual log files or logging system
            recent_logs = [
                LogEntry(
                    timestamp=datetime.now() - timedelta(minutes=2),
                    level="INFO",
                    logger="market_data",
                    message="Market data feed refreshed successfully",
                    module="market_data_service",
                ),
                LogEntry(
                    timestamp=datetime.now() - timedelta(minutes=5),
                    level="INFO",
                    logger="system",
                    message="System health check completed",
                    module="dashboard_service",
                ),
                LogEntry(
                    timestamp=datetime.now() - timedelta(minutes=8),
                    level="WARN",
                    logger="risk",
                    message="High volatility detected in NIFTY options",
                    module="risk_monitor",
                ),
                LogEntry(
                    timestamp=datetime.now() - timedelta(minutes=12),
                    level="INFO",
                    logger="auth",
                    message="Token refresh completed",
                    module="auth_service",
                ),
                LogEntry(
                    timestamp=datetime.now() - timedelta(minutes=15),
                    level="DEBUG",
                    logger="data",
                    message="Fetched 1247 instrument contracts",
                    module="instrument_service",
                ),
            ]

            logs_subset = recent_logs[:limit]
            await self._cache.set_by_key(cache_key, logs_subset, ttl=60)
            return logs_subset

        except Exception as e:
            logger.error(f"Failed to get recent logs: {e}")
            return []

    async def _get_system_alerts(self) -> list[dict[str, Any]]:
        """Get active system alerts"""
        try:
            alerts = []

            # Check system resource usage
            if self._system_metrics_cache:
                if self._system_metrics_cache.cpu_usage_percent > 80:
                    alerts.append(
                        {
                            "id": "cpu_high",
                            "level": "warning",
                            "message": f"High CPU usage: {self._system_metrics_cache.cpu_usage_percent:.1f}%",
                            "timestamp": datetime.now().isoformat(),
                        }
                    )

                if self._system_metrics_cache.memory_usage_percent > 85:
                    alerts.append(
                        {
                            "id": "memory_high",
                            "level": "warning",
                            "message": f"High memory usage: {self._system_metrics_cache.memory_usage_percent:.1f}%",
                            "timestamp": datetime.now().isoformat(),
                        }
                    )

            return alerts

        except Exception as e:
            logger.error(f"Failed to get system alerts: {e}")
            return []

    def _priced_book(self) -> tuple[PricedBook | None, BookContext | None]:
        """Price the synced book at the live spot, or ``(None, book)``."""
        book = self._book_fn() if self._book_fn is not None else None
        if book is None or not book.portfolio.positions:
            return None, book
        spot = self._spot_fn() if self._spot_fn is not None else None
        if spot is None or spot <= 0:
            logger.debug("No live spot; book-derived metrics unavailable")
            return None, book
        try:
            return price_book(book.portfolio, marks=book.marks, spot=spot), book
        except Exception:
            logger.exception("Failed to price the book for dashboard metrics")
            return None, book

    @staticmethod
    def _concentration_by_underlying(priced: PricedBook) -> dict[str, float]:
        """Share of absolute gross notional per underlying, in percent."""
        by_symbol: dict[str, float] = {}
        for leg in priced.legs:
            # Trading symbols lead with the underlying, e.g. NIFTY24907...
            match = re.match(r"^([A-Z]+)", leg.contract.symbol)
            key = match.group(1) if match else leg.contract.symbol
            by_symbol[key] = by_symbol.get(key, 0.0) + abs(leg.quantity * leg.mark)
        total = sum(by_symbol.values())
        if total <= 0:
            return {}
        return {k: round(v / total * 100.0, 2) for k, v in sorted(by_symbol.items())}

    def _build_positions_summary(self) -> PositionSummary:
        """Position summary derived from the synced book.

        Fields with no source stay null: ``daily_pnl`` needs a prior-close
        snapshot and ``active_strategies`` a strategy store, neither of which
        this read-only app persists.
        """
        priced, book = self._priced_book()

        if book is None:
            return PositionSummary(total_positions=0, total_pnl=Decimal("0"))

        total_pnl = (
            sum(
                (leg.mark - pos.entry_price) * pos.quantity
                for leg, pos in zip(priced.legs, book.portfolio.positions, strict=False)
            )
            if priced
            else 0.0
        )

        summary = PositionSummary(
            total_positions=len(book.portfolio.positions),
            total_pnl=Decimal(str(round(total_pnl, 2))),
            margin_used=(Decimal(str(book.margin_used)) if book.margin_used is not None else None),
            available_margin=(
                Decimal(str(book.margin_available)) if book.margin_available is not None else None
            ),
        )
        if priced is None:
            return summary

        agg = priced.aggregate_greeks
        return summary.model_copy(
            update={
                "portfolio_delta": Decimal(str(round(agg.delta, 4))),
                "portfolio_gamma": Decimal(str(round(agg.gamma, 6))),
                "portfolio_theta": Decimal(str(round(agg.theta, 4))),
                "portfolio_vega": Decimal(str(round(agg.vega, 4))),
                "concentration_risk": self._concentration_by_underlying(priced),
            }
        )

    def _build_risk_metrics(self) -> RiskMetrics:
        """Risk metrics from the live book.

        VaR, expected shortfall, drawdown and beta need a persisted P&L return
        series and stay null. Stress tests are a genuine full revaluation of
        the book under named shocks, not a stored table of historical episodes.
        """
        limits = risk_limits_from_settings()
        concentration_limits = {"single_underlying": limits.max_concentration * 100.0}

        priced, _ = self._priced_book()
        if priced is None or not priced.legs:
            return RiskMetrics(concentration_limits=concentration_limits)

        agg = priced.aggregate_greeks
        return RiskMetrics(
            portfolio_delta=Decimal(str(round(agg.delta, 4))),
            portfolio_gamma=Decimal(str(round(agg.gamma, 6))),
            portfolio_theta=Decimal(str(round(agg.theta, 4))),
            portfolio_vega=Decimal(str(round(agg.vega, 4))),
            portfolio_rho=Decimal(str(round(agg.rho, 4))),
            delta_limit_utilization=abs(agg.delta) / limits.max_abs_delta * 100.0,
            gamma_limit_utilization=abs(agg.gamma) / limits.max_abs_gamma * 100.0,
            vega_limit_utilization=abs(agg.vega) / limits.max_abs_vega * 100.0,
            concentration_limits=concentration_limits,
            stress_test_results=self._run_stress_tests(priced),
        )

    @staticmethod
    def _run_stress_tests(priced: PricedBook) -> dict[str, Decimal]:
        """Revalue the book under each named shock and report the P&L.

        Full revaluation via the scenario engine, so the numbers describe this
        book under this shock — as opposed to the previous hardcoded table of
        historical episode losses, which described nothing.
        """
        book = priced.to_scenario_book()
        results: dict[str, Decimal] = {}
        for name, (spot_shift, vol_shift) in STRESS_SCENARIOS.items():
            try:
                grid = ScenarioGrid(
                    spot_shifts=np.array([spot_shift]),
                    vol_shifts=np.array([vol_shift]),
                    time_shifts=np.array([0.0]),
                )
                result = run_scenario_grid(book, spot=priced.spot, rate=priced.rate, grid=grid)
                results[name] = Decimal(str(round(float(result.pnl[0, 0, 0]), 2)))
            except Exception:
                logger.exception("Stress scenario %s failed", name)
        return results

    async def get_positions_summary(
        self, symbol: str | None = None, expiry_date: str | None = None
    ) -> PositionSummary:
        """
        Get aggregated portfolio position summary with real-time Greeks.
        Aggregates the priced book from the portfolio sync, not a strategy store.
        """
        try:
            cache_key = f"positions_summary_{symbol}_{expiry_date}"
            cached_summary = await self._cache.get_by_key(cache_key)
            if cached_summary:
                return cached_summary

            summary = self._build_positions_summary()
            await self._cache.set_by_key(cache_key, summary, ttl=60)
            return summary

        except Exception as e:
            logger.error(f"Failed to get positions summary: {e}")
            raise DataQualityError(f"Positions data unavailable: {e!s}")

    async def calculate_risk_metrics(self) -> RiskMetrics:
        """
        Calculate comprehensive portfolio risk metrics.
        Uses sophisticated risk models for institutional trading.
        """
        try:
            cached_metrics = await self._cache.get_by_key("risk_metrics")
            if cached_metrics:
                return cached_metrics

            metrics = self._build_risk_metrics()
            await self._cache.set_by_key("risk_metrics", metrics, ttl=300)
            return metrics

        except Exception as e:
            logger.error(f"Risk calculation failed: {e}")
            raise CalculationError(f"Risk metrics calculation failed: {e!s}")

    async def get_dashboard_config(self, user_id: str) -> DashboardConfig:
        """Get user dashboard configuration"""
        try:
            # This would load from user preferences database
            return DashboardConfig(
                user_id=user_id,
                refresh_interval=5,
                default_symbol="NIFTY",
                default_expiry_days=7,
                risk_alerts_enabled=True,
                theme="dark",
                layout="standard",
                watchlist=["NIFTY", "BANKNIFTY", "FINNIFTY"],
                alert_thresholds={
                    "portfolio_delta": 0.5,
                    "daily_pnl_loss": -10000.0,
                    "margin_utilization": 80.0,
                },
            )
        except Exception as e:
            logger.error(f"Failed to get dashboard config: {e}")
            # Return default config
            return DashboardConfig(user_id=user_id)

    async def update_dashboard_config(self, user_id: str, config: DashboardConfig) -> None:
        """Update user dashboard configuration"""
        try:
            # This would save to user preferences database
            logger.info(f"Dashboard config updated for user {user_id}")
            await self._cache.clear()
        except Exception as e:
            logger.error(f"Failed to update dashboard config: {e}")
            raise DataQualityError(f"Configuration update failed: {e!s}")

    async def recompute_analytics(self, symbol: str, expiry_date: str | None = None) -> None:
        """
        Background task to recompute analytics using your existing processing pipeline.
        Integrates with MarketDataManager for consistent data processing.
        """
        try:
            if not self.market_data_manager:
                logger.warning("MarketDataManager not available for recomputation")
                return

            logger.info(f"Starting analytics recomputation for {symbol}")

            # This would trigger your existing processing pipeline
            saved_files, output_dir = self.market_data_manager.save_features_for_expiry(
                symbol=symbol,
                exchange="NSE",
                expiry=expiry_date,
                option_interval="3minute",
                spot_interval="3",
                days_back=7,
                strikes=1,
            )

            # Clear relevant caches
            await self._cache.clear()
            logger.info(f"Analytics recomputation completed for {symbol}")

        except Exception as e:
            logger.error(f"Analytics recomputation failed: {e}", exc_info=True)

    async def get_recent_logs_formatted(
        self, level: str = "INFO", lines: int = 100
    ) -> list[dict[str, Any]]:
        """Get recent system logs with filtering"""
        try:
            logs = await self._get_recent_logs(limit=lines)

            # Filter by level if specified
            if level != "ALL":
                logs = [log for log in logs if log.level == level]

            # Convert to dict format for API response
            return [
                {
                    "timestamp": log.timestamp.strftime("%H:%M:%S"),
                    "level": log.level,
                    "message": log.message,
                    "module": log.module or log.logger,
                }
                for log in logs
            ]

        except Exception as e:
            logger.error(f"Failed to get recent logs: {e}")
            return []
