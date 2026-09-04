# src/options_trading/config/settings.py
"""
Production-grade application settings for Options Trading.
- Uses Pydantic V2 BaseSettings for environment variables and validation.
- Merges API endpoints, defaults, risk params, token management, and logging.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with validation and environment variable support."""

    # -------------------------------------------------------------------------
    # Pydantic Config
    # -------------------------------------------------------------------------
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -------------------------------------------------------------------------
    # Environment & Logging
    # -------------------------------------------------------------------------
    environment: str = Field(default="production", description="Environment")
    debug: bool = Field(default=False, description="Debug mode")
    log_level: str = Field(default="INFO", description="Logging level")

    log_format: str = Field(
        default="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        description="Log format string",
    )
    log_date_format: str = Field(default="%Y-%m-%d %H:%M:%S")
    log_file_max_size_mb: int = Field(default=10)
    log_backup_count: int = Field(default=5)

    log_dir: Path = Field(default=Path.cwd() / "logs")
    main_log_file: str = Field(default="pairs_trading.log")
    trade_log_file: str = Field(default="trades.log")
    error_log_file: str = Field(default="errors.log")

    live_trading_log_dir: Path = Field(default=Path.cwd() / "logs" / "live_trading")
    market_data_log_file: str = Field(default="market_data_stream.log")
    trade_execution_log_file: str = Field(default="trade_execution.log")

    # -------------------------------------------------------------------------
    # Upstox API Configuration
    # -------------------------------------------------------------------------
    upstox_api_key: str = Field(..., description="Upstox API key")
    upstox_secret_key: str = Field(..., description="Upstox secret key")

    upstox_base_url: str = Field(default="https://api.upstox.com")
    upstox_expired_expiries_url: str = Field(
        default="https://api.upstox.com/v2/expired-instruments/expiries"
    )
    upstox_expired_contracts_url: str = Field(
        default="https://api.upstox.com/v2/expired-instruments/option/contract"
    )
    upstox_option_candles_url: str = Field(
        default="https://api.upstox.com/v2/expired-instruments/historical-candle"
    )
    upstox_spot_candles_url: str = Field(default="https://api.upstox.com/v3/historical-candle")
    instrument_key_url: str = Field(
        default="https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
    )
    upstox_auth_url: str = Field(default="https://api.upstox.com/v2/login/authorization/token")
    upstox_profile_url: str = Field(default="https://api.upstox.com/v2/user/profile")
    upstox_oauth_dialog_url: str = Field(
        default="https://api-v2.upstox.com/login/authorization/dialog"
    )
    # src/options_trading/config/settings.py
    upstox_option_contracts_url: str = Field(default="https://api.upstox.com/v2/option/contract")
    upstox_option_chain_url: str = Field(default="https://api.upstox.com/v2/option/chain")

    default_api_version: str = Field(default="2.0")
    default_content_type: str = Field(default="application/x-www-form-urlencoded")

    # -------------------------------------------------------------------------
    # OAuth & Authentication
    # -------------------------------------------------------------------------
    secret_key: str = Field(
        default="", description="JWT secret key — must be set via SECRET_KEY env var"
    )
    oauth_redirect_uri: str = Field(
        default="http://localhost:8000/api/v1/auth/callback",
        description="OAuth2 redirect URI",
    )
    oauth_timeout_seconds: int = Field(default=120)
    default_redirect_port: int = Field(default=8000)
    local_server_port: int = Field(default=8000)

    access_token_expire_minutes: int = Field(default=30)
    token_storage_dir: Path = Field(default=Path.home())
    token_storage_file: str = Field(default=".upstox_tokens.json")
    token_file_name: str = Field(default=".upstox_pairs_tokens.json")
    token_keyring_service: str = Field(default="efs_upstox")
    token_keyring_username: str = Field(default="access_info")
    token_expiry_buffer_minutes: int = Field(default=5)
    max_token_age_days: int = Field(default=7)
    validation_api_timeout: int = Field(default=10)

    AUTH_VALIDATE_TIMEOUT: int = Field(default=5)
    AUTH_REFRESH_TIMEOUT: int = Field(default=8)
    AUTH_NEAR_EXPIRY_SECONDS: int = Field(default=300)
    TRUST_STORED_TOKEN_ON_NETWORK_ERROR: bool = Field(default=True)

    # -------------------------------------------------------------------------
    # API Configuration
    # -------------------------------------------------------------------------
    api_timeout_seconds: int = Field(default=30)
    max_concurrent_requests: int = Field(default=5)
    api_calls_per_minute: int = Field(default=50)

    # -------------------------------------------------------------------------
    # Database & Redis
    # -------------------------------------------------------------------------
    database_url: str | None = Field(default=None)
    redis_url: str = Field(default="redis://localhost:6379/0")
    redis_timeout: int = Field(default=5)

    # -------------------------------------------------------------------------
    # Market Data Defaults
    # -------------------------------------------------------------------------
    default_option_interval: str = Field(default="3minute")
    default_spot_interval: str = Field(default="3")
    default_strikes: int = Field(default=1)
    default_days_back: int = Field(default=4)
    exchange: str = Field(default="NSE_INDEX")
    trading_symbol: str = Field(default="Nifty 50")
    expiry_start_index: int = Field(default=0)
    expiry_end_index: int | None = Field(default=None)
    latest_expiry: int = Field(default=5)
    default_trim_hours: int = Field(default=2)
    OUT_BASE_PREFIX: str = Field(default="FINAL", description="Output base folder prefix")
    JOB_REGISTRY_PATH: str = Field(
        default=".job_registry.json", description="Job registry path (JSON)"
    )
    snapshot_store_path: str = Field(
        default="runtime_data/snapshots",
        description="Root directory for clean option-chain Parquet snapshots",
    )
    capture_interval_seconds: int = Field(
        default=900,
        description="Default cadence for the unattended capture scheduler (seconds)",
    )
    capture_autostart: bool = Field(
        default=True,
        description=(
            "Start the capture schedule automatically once a valid Upstox token exists. "
            "Without it there is no live analytics source and the dashboard shows demo data."
        ),
    )
    capture_autostart_underlying: str = Field(
        default="NIFTY",
        description="Underlying the capture schedule auto-starts on",
    )
    capture_autostart_expiry_index: int = Field(
        default=0,
        ge=0,
        description="Which expiry to auto-capture; 0 = nearest tradable expiry",
    )
    # -------------------------------------------------------------------------
    # History analytics (VRP signal, backtest, P&L explain)
    # -------------------------------------------------------------------------
    # These panels replay the captured Parquet history rather than the live
    # chain, refitting a vol surface per stored day. That is orders of
    # magnitude more work than the per-tick builders, so they run on their own
    # cadence with a cached result and never on the capture tick.
    book_snapshot_store_path: str = Field(
        default="runtime_data/book_snapshots",
        description=(
            "Root directory for persisted book snapshots. Feeds P&L explain, "
            "which needs the book as it stood at the start of a period. "
            "Contains position detail, so it lives under gitignored runtime_data."
        ),
    )
    history_refresh_seconds: float = Field(
        default=1800.0,
        gt=0,
        description=(
            "Minimum seconds between history-analytics recomputes. The result "
            "is daily-resolution, so refreshing faster only burns CPU."
        ),
    )
    history_surface_model: Literal["spline", "essvi"] = Field(
        default="spline",
        description=(
            "Surface fitted per replayed day. 'spline' is ~3x faster than "
            "'essvi' and the VRP signal only reads the ATM vol, where the two "
            "agree closely; 'essvi' buys arbitrage-free wings the ATM-only "
            "features do not use."
        ),
    )
    history_rv_window: int = Field(
        default=21,
        ge=3,
        description="Trailing EOD closes fed to the close-to-close realized-vol estimator",
    )
    history_tenor_days: int = Field(
        default=30,
        ge=1,
        description="Option tenor (calendar days, ACT/365) for the ATM IV and skew features",
    )
    history_walk_forward_folds: int = Field(
        default=4,
        ge=1,
        description="Rolling walk-forward folds; more folds need more captured days",
    )
    history_walk_forward_train_frac: float = Field(
        default=0.6,
        gt=0.0,
        lt=1.0,
        description="Fraction of each fold's window used to select the config",
    )
    history_backtest_initial_equity: float = Field(
        default=1_000_000.0,
        gt=0,
        description="Starting cash for the replayed VRP backtest",
    )
    history_backtest_lot_size: int = Field(
        default=75,
        ge=1,
        description="Lot size the replayed strategy trades (NIFTY F&O lot)",
    )
    history_vrp_entry_grid: tuple[float, ...] = Field(
        default=(0.02, 0.03, 0.05),
        description=(
            "entry_vrp_min values searched per walk-forward fold. Every value "
            "counts as a trial in the deflated Sharpe, so a wider grid is "
            "penalised, not rewarded."
        ),
    )
    # The replayed backtest is a separate hypothetical account, so it gets its
    # own risk limits rather than the live book's: reusing risk_max_abs_* would
    # apply caps sized for the user's real positions to a 10-lakh paper
    # account. The caps are derived from equity as risk budgets (see
    # backtest_risk_limits) so they scale with initial_equity instead of being
    # magic numbers that silently forbid every trade.
    history_vega_budget_frac: float = Field(
        default=0.005,
        gt=0,
        description=(
            "Max fraction of backtest equity the book may lose to a "
            "1-vol-point move. Sized so the replayed strategy can actually "
            "trade: test_permits_the_strategy_it_is_meant_to_run pins that."
        ),
    )
    history_delta_budget_frac: float = Field(
        default=0.02,
        gt=0,
        description="Max fraction of backtest equity exposed to a 1% spot move",
    )
    history_gamma_budget_frac: float = Field(
        default=0.005,
        gt=0,
        description="Max fraction of backtest equity in the second-order term of a 1% spot move",
    )
    history_backtest_max_drawdown: float = Field(
        default=0.25,
        gt=0,
        le=1.0,
        description="Drawdown kill-switch for the replayed backtest account",
    )
    history_backtest_max_concentration: float = Field(
        default=1.0,
        gt=0,
        le=1.0,
        description=(
            "Concentration cap for the replayed backtest. A single-strategy "
            "straddle book is ~50% concentrated per leg by construction, so a "
            "portfolio-level cap below that forbids the strategy outright."
        ),
    )
    history_max_explain_gap_days: float = Field(
        default=4.0,
        gt=0,
        description=(
            "Largest gap, in days, between the two end-of-day books a P&L "
            "attribution will compare. Spans a long weekend plus a holiday. "
            "Beyond it the pair is not a daily move: theta would accrue over "
            "weeks, on options that may have expired inside the gap."
        ),
    )
    history_band_proportional_cost: float = Field(
        default=5e-4,
        ge=0,
        description="Hedge cost as a fraction of traded value in the Whalley-Wilmott band",
    )
    history_band_risk_aversion: float = Field(
        default=1.0,
        gt=0,
        description="CARA risk aversion in the Whalley-Wilmott band; higher hedges tighter",
    )

    # -------------------------------------------------------------------------
    # Paper desk (optitrade.desk.cycle over the captured history)
    # -------------------------------------------------------------------------
    # The desk trades a NOTIONAL account with PAPER fills computed inside the
    # quant core. There is no order-placement path in this application and
    # these settings do not create one; the Upstox connection stays read-only.
    # The desk reuses the history replay settings (underlying, surface,
    # rv_window, tenor_days) and the paper-account risk budgets above, so the
    # panel it shows and the backtest it is compared against cannot silently
    # describe different markets.
    desk_state_path: str = Field(
        default="runtime_data/desk_state.json",
        description=(
            "Persisted paper-desk state: the book, the notional account, and "
            "which captured dates have been cycled. Restoring it is what stops "
            "a restart from re-entering positions the desk already holds. "
            "Contains position detail, so it lives under gitignored runtime_data."
        ),
    )
    desk_journal_dir: str = Field(
        default="runtime_data/desk_journal",
        description=(
            "Directory for the desk's append-only event journal. One stable "
            "run id accumulates across restarts so the correlation ids stored "
            "in past cycle records stay resolvable (ADR-009)."
        ),
    )
    desk_kill_switch_path: str = Field(
        default="runtime_data/HALT",
        description=(
            "Marker file for the desk kill switch; engaged iff it exists. Any "
            "process or a human with `touch` can halt the desk, and the halt "
            "survives a restart."
        ),
    )
    desk_initial_equity: float = Field(
        default=1_000_000.0,
        gt=0,
        description="Starting equity of the desk's notional paper account (INR)",
    )
    desk_lot_size: int = Field(
        default=75,
        ge=1,
        description="Contract lot size the desk trades; NIFTY options are 75 per lot",
    )
    desk_quantity: float = Field(
        default=1.0,
        gt=0,
        description="Lots sold per leg when the desk enters a short-vol structure",
    )
    desk_entry_vrp_min: float = Field(
        default=0.03,
        description=(
            "Minimum variance risk premium (atm_iv - realized_vol, in decimal "
            "vol) before the desk opens a structure"
        ),
    )
    desk_exit_vrp_max: float = Field(
        default=0.0,
        description="VRP level at or below which the desk buys the structure back",
    )
    desk_spread_frac: float = Field(
        default=0.005,
        ge=0,
        description=(
            "Full relative bid-ask spread assumed for paper fills; each fill "
            "pays half of it, so buys pay up and sells receive less. This is "
            "the honesty margin on a simulated fill, not a broker charge."
        ),
    )
    desk_require_debate: bool = Field(
        default=True,
        description=(
            "Run the governance debate panel before the risk engine. The "
            "fail-closed risk review runs regardless (ADR-008)."
        ),
    )
    desk_refresh_seconds: float = Field(
        default=300.0,
        gt=0,
        description=(
            "Minimum seconds before a desk advance refits the replay. The "
            "captured history gains at most one end-of-day snapshot per date, "
            "so refitting faster only burns CPU."
        ),
    )
    desk_max_cycles_retained: int = Field(
        default=250,
        ge=1,
        description=(
            "Cycle summaries kept in the desk state file (~one trading year). "
            "The journal remains the complete audit trail; this bounds only "
            "what the panel tabulates."
        ),
    )

    # -------------------------------------------------------------------------
    # Analyst panel (deterministic analysts + groundedness audit)
    # -------------------------------------------------------------------------
    # The analysts read the desk journal and explain it; they never trade. The
    # four thresholds below are the analysts' own flag levels, lifted out of
    # the quant core so a deployment can retune what counts as noteworthy
    # without editing it.
    analyst_journal_run_id: str = Field(
        default="desk",
        description=(
            "Run id of the journal the analysts read. Must match the id the "
            "desk writes under, or the panel audits an empty journal."
        ),
    )
    analyst_refresh_seconds: float = Field(
        default=60.0,
        gt=0,
        description=(
            "Minimum seconds before the analysts re-read the journal. Also "
            "recomputed whenever the journal file changes, so this only "
            "bounds repeat work between appends."
        ),
    )
    analyst_surface_rmse_threshold: float = Field(
        default=0.5,
        gt=0,
        description=(
            "Vol points of eSSVI fit RMSE above which the surface auditor "
            "flags the surface as not safe to quote from"
        ),
    )
    analyst_min_explained_fraction: float = Field(
        default=0.9,
        gt=0,
        le=1,
        description=(
            "Fraction of a day's P&L the explain must decompose before the "
            "post-mortem analyst stops flagging the residual"
        ),
    )
    analyst_high_vrp: float = Field(
        default=0.04,
        description=(
            "Variance risk premium (decimal vol) above which the regime "
            "analyst flags a rich-premium regime for vol sellers"
        ),
    )
    analyst_steep_term: float = Field(
        default=0.05,
        description=(
            "Term-slope magnitude (decimal vol) above which the regime "
            "analyst flags a steep or deeply inverted term structure"
        ),
    )
    analyst_deep_skew: float = Field(
        default=0.03,
        description=(
            "25-delta skew magnitude (decimal vol) above which the regime "
            "analyst flags pronounced skew"
        ),
    )

    # -------------------------------------------------------------------------
    # Financial Parameters
    # -------------------------------------------------------------------------
    risk_free_rate: float = Field(default=0.0679)
    # Book risk limits. These feed optitrade.risk.RiskLimits; they are config,
    # never literals in a flow (CLAUDE.md rule 2). Delta/gamma/vega are in
    # underlying units per the core Greeks conventions (vega per unit vol).
    risk_max_abs_delta: float = Field(default=500.0, gt=0)
    risk_max_abs_gamma: float = Field(default=50.0, gt=0)
    risk_max_abs_vega: float = Field(default=10_000.0, gt=0)
    risk_max_drawdown: float = Field(default=0.05, gt=0, le=1.0)
    risk_max_concentration: float = Field(default=0.35, gt=0, le=1.0)
    risk_margin_buffer: float = Field(default=1.25, ge=1.0)
    periods_per_year: int = Field(default=252)
    rv_window_1min: int = Field(default=390)  # ≈ 1.5 trading days
    rv_window_3min: int = Field(default=130)  # ≈ 1.5 trading days
    rv_buffer_weeks: int = Field(default=2)
    min_rv_coverage: float = Field(default=0.8)
    # -------------------------------------------------------------------------
    # Cache
    # -------------------------------------------------------------------------
    cache_max_size: int = Field(default=100)
    cache_ttl_seconds: int = Field(default=3600)

    # -------------------------------------------------------------------------
    # Validators
    # -------------------------------------------------------------------------
    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        allowed = ["development", "staging", "production", "testing"]
        if v not in allowed:
            raise ValueError(f"Environment must be one of {allowed}")
        return v

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in allowed:
            raise ValueError(f"Log level must be one of {allowed}")
        return v.upper()

    @field_validator("upstox_api_key", "upstox_secret_key")
    @classmethod
    def validate_api_credentials(cls, v: str) -> str:
        if not v or v.strip() == "":
            raise ValueError("API credentials cannot be empty")
        return v

    @field_validator("oauth_redirect_uri")
    @classmethod
    def validate_redirect_uri(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("Redirect URI must start with http:// or https://")
        return v

    # -------------------------------------------------------------------------
    # Convenience Properties
    # -------------------------------------------------------------------------
    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_testing(self) -> bool:
        return self.environment == "testing"


@lru_cache
def get_settings() -> Settings:
    """Get application settings (cached)."""
    return Settings()


# Module-level settings instance
settings = get_settings()
