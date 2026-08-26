# src/options_trading/models/dashboard.py
"""
Advanced Pydantic models for dashboard data structures.
Designed for institutional-grade options trading platform.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class AlertLevel(str, Enum):
    """Alert severity levels"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SystemHealth(str, Enum):
    """System health status"""

    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    OFFLINE = "offline"


class StrategyStatus(str, Enum):
    """Strategy execution status"""

    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


class ConnectionStatus(str, Enum):
    """Connection status for external services"""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


class AuthenticationStatus(BaseModel):
    """Authentication system status"""

    is_authenticated: bool
    user_id: str
    user_name: str | None = None
    token_expires_at: datetime | None = None
    time_until_expiry: str | None = None
    last_login: datetime | None = None
    permissions: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def calculate_time_until_expiry(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("token_expires_at"):
            delta = data["token_expires_at"] - datetime.now()
            if delta.total_seconds() > 0:
                hours, remainder = divmod(int(delta.total_seconds()), 3600)
                minutes, _ = divmod(remainder, 60)
                data["time_until_expiry"] = f"{hours}h {minutes}m"
            else:
                data["time_until_expiry"] = "Expired"
        elif isinstance(data, dict) and not data.get("time_until_expiry"):
            data["time_until_expiry"] = "Expired"
        return data


class MarketDataFeed(BaseModel):
    """Individual market data feed status"""

    name: str
    status: ConnectionStatus
    instruments_count: int
    last_update: datetime | None = None
    latency_ms: float | None = None
    error_rate: float = Field(default=0.0, ge=0.0, le=100.0)


class MarketDataStatus(BaseModel):
    """Market data system status"""

    overall_status: ConnectionStatus
    feeds_connected: int
    total_instruments: int
    data_quality: str = "excellent"
    last_update: datetime | None = None
    feeds: list[MarketDataFeed] = Field(default_factory=list)
    response_time_ms: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="before")
    @classmethod
    def determine_data_quality(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        feeds = data.get("feeds", [])
        if not feeds:
            data["data_quality"] = "unknown"
            return data
        total_error_rate = sum(
            f.error_rate if hasattr(f, "error_rate") else f.get("error_rate", 0) for f in feeds
        ) / len(feeds)
        if total_error_rate <= 1.0:
            data["data_quality"] = "excellent"
        elif total_error_rate <= 5.0:
            data["data_quality"] = "good"
        elif total_error_rate <= 15.0:
            data["data_quality"] = "fair"
        else:
            data["data_quality"] = "poor"
        return data


class GreeksSnapshot(BaseModel):
    """Greeks values snapshot"""

    delta: Decimal | None = None
    gamma: Decimal | None = None
    theta: Decimal | None = None
    vega: Decimal | None = None
    rho: Decimal | None = None
    timestamp: datetime = Field(default_factory=datetime.now)


class PositionData(BaseModel):
    """Individual position data"""

    symbol: str
    strike: Decimal
    expiry_date: date
    option_type: str  # CE or PE
    quantity: int
    entry_price: Decimal
    current_price: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    greeks: GreeksSnapshot | None = None
    implied_volatility: Decimal | None = None


class StrategyPerformance(BaseModel):
    """Real-time strategy performance metrics"""

    strategy_id: str
    name: str
    status: StrategyStatus
    positions_count: int
    total_pnl: Decimal
    daily_pnl: Decimal
    pnl_percentage: Decimal
    risk_level: str
    last_rebalance: datetime | None = None
    max_drawdown: Decimal | None = None
    sharpe_ratio: Decimal | None = None
    portfolio_greeks: GreeksSnapshot | None = None
    positions: list[PositionData] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def calculate_risk_level(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        pnl_pct = data.get("pnl_percentage", 0)
        if abs(float(pnl_pct)) <= 2.0:
            data["risk_level"] = "low"
        elif abs(float(pnl_pct)) <= 5.0:
            data["risk_level"] = "medium"
        else:
            data["risk_level"] = "high"
        return data


class PositionSummary(BaseModel):
    """Aggregated position summary"""

    total_positions: int
    active_strategies: int
    total_pnl: Decimal
    daily_pnl: Decimal
    margin_used: Decimal
    available_margin: Decimal
    portfolio_delta: Decimal
    portfolio_gamma: Decimal
    portfolio_theta: Decimal
    portfolio_vega: Decimal
    concentration_risk: dict[str, float] = Field(default_factory=dict)


class RiskMetrics(BaseModel):
    """Comprehensive risk metrics"""

    var_1d: Decimal  # 1-day Value at Risk
    var_1d_percentage: Decimal
    expected_shortfall: Decimal
    maximum_drawdown: Decimal
    beta: Decimal | None = None
    portfolio_delta: Decimal
    portfolio_gamma: Decimal
    portfolio_theta: Decimal
    portfolio_vega: Decimal
    portfolio_rho: Decimal
    delta_limit_utilization: float = Field(ge=0.0, le=100.0)
    gamma_limit_utilization: float = Field(ge=0.0, le=100.0)
    vega_limit_utilization: float = Field(ge=0.0, le=100.0)
    concentration_limits: dict[str, float] = Field(default_factory=dict)
    stress_test_results: dict[str, Decimal] = Field(default_factory=dict)


class SystemMetrics(BaseModel):
    """System performance metrics"""

    uptime: str
    cpu_usage_percent: float = Field(ge=0.0, le=100.0)
    memory_usage_percent: float = Field(ge=0.0, le=100.0)
    disk_usage_percent: float = Field(ge=0.0, le=100.0)
    api_response_time_ms: float = Field(ge=0.0)
    error_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    last_backup: datetime | None = None


class LogEntry(BaseModel):
    """System log entry"""

    timestamp: datetime
    level: str
    logger: str
    message: str
    module: str | None = None
    function: str | None = None
    line_number: int | None = None


class SystemStatus(BaseModel):
    """Comprehensive system status"""

    overall_health: SystemHealth
    timestamp: datetime = Field(default_factory=datetime.now)
    authentication: AuthenticationStatus
    market_data: MarketDataStatus
    system_metrics: SystemMetrics
    recent_logs: list[LogEntry] = Field(default_factory=list)
    alerts: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def determine_overall_health(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        auth = data.get("authentication")
        market_data = data.get("market_data")
        system_metrics = data.get("system_metrics")

        health = SystemHealth.HEALTHY

        if (
            not auth
            or (hasattr(auth, "is_authenticated") and not auth.is_authenticated)
            or (isinstance(auth, dict) and not auth.get("is_authenticated"))
        ):
            health = SystemHealth.CRITICAL
        elif market_data:
            status = (
                market_data.overall_status
                if hasattr(market_data, "overall_status")
                else market_data.get("overall_status")
            )
            if status == ConnectionStatus.DISCONNECTED or status == "disconnected":
                health = SystemHealth.CRITICAL

        if health == SystemHealth.HEALTHY and system_metrics:
            cpu = (
                system_metrics.cpu_usage_percent
                if hasattr(system_metrics, "cpu_usage_percent")
                else system_metrics.get("cpu_usage_percent", 0)
            )
            mem = (
                system_metrics.memory_usage_percent
                if hasattr(system_metrics, "memory_usage_percent")
                else system_metrics.get("memory_usage_percent", 0)
            )
            errs = (
                system_metrics.error_count
                if hasattr(system_metrics, "error_count")
                else system_metrics.get("error_count", 0)
            )
            if cpu > 90 or mem > 90 or errs > 10:
                health = SystemHealth.CRITICAL
            elif cpu > 70 or mem > 70 or errs > 5:
                health = SystemHealth.WARNING

        data["overall_health"] = health
        return data


class DashboardConfig(BaseModel):
    """Dashboard configuration and user preferences"""

    user_id: str
    refresh_interval: int = Field(default=5, ge=1, le=60)  # seconds
    default_symbol: str = "NIFTY"
    default_expiry_days: int = Field(default=7, ge=0, le=365)
    risk_alerts_enabled: bool = True
    email_notifications: bool = False
    mobile_notifications: bool = False
    theme: str = "dark"
    layout: str = "standard"
    widgets_config: dict[str, Any] = Field(default_factory=dict)
    watchlist: list[str] = Field(default_factory=list)
    alert_thresholds: dict[str, float] = Field(default_factory=dict)


class RealtimeMetrics(BaseModel):
    """Real-time streaming metrics"""

    timestamp: datetime = Field(default_factory=datetime.now)
    total_pnl: Decimal
    daily_pnl: Decimal
    active_positions: int
    portfolio_delta: Decimal
    portfolio_gamma: Decimal
    portfolio_theta: Decimal
    portfolio_vega: Decimal
    margin_utilization: float
    largest_position_pnl: Decimal
    largest_position_symbol: str
    volatility_index: Decimal | None = None
    market_sentiment: str | None = None


class WebSocketMessage(BaseModel):
    """WebSocket message structure"""

    type: str
    data: dict[str, Any]
    timestamp: datetime = Field(default_factory=datetime.now)
    client_id: str | None = None


class SubscriptionRequest(BaseModel):
    """WebSocket subscription request"""

    symbols: list[str] = Field(default_factory=list)
    data_types: list[str] = Field(default_factory=list)
    update_frequency: int = Field(default=1, ge=1, le=60)  # seconds


class AlertConfig(BaseModel):
    """Alert configuration"""

    alert_id: str
    name: str
    condition: str
    threshold: float
    level: AlertLevel
    enabled: bool = True
    created_at: datetime = Field(default_factory=datetime.now)
    triggered_at: datetime | None = None
