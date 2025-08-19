# src/options_trading/config/settings.py
"""
Production-grade application settings for Options Trading.
- Uses Pydantic V2 BaseSettings for environment variables and validation.
- Merges API endpoints, defaults, risk params, token management, and logging.
"""

import os
from pathlib import Path
from functools import lru_cache
from typing import Optional

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
    upstox_spot_candles_url: str = Field(
        default="https://api.upstox.com/v3/historical-candle"
    )
    instrument_key_url: str = Field(
        default="https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
    )
    upstox_auth_url: str = Field(
        default="https://api.upstox.com/v2/login/authorization/token"
    )
    upstox_profile_url: str = Field(default="https://api.upstox.com/v2/user/profile")
    upstox_charges_url: str = Field(default="https://api.upstox.com/v2/charges/brokerage")
    upstox_margin_url: str = Field(default="https://api.upstox.com/v2/charges/margin")
    upstox_oauth_dialog_url: str = Field(
        default="https://api-v2.upstox.com/login/authorization/dialog"
    )
    # src/options_trading/config/settings.py
    upstox_option_contracts_url: str = Field(default="https://api.upstox.com/v2/option/contract")
    upstox_option_chain_url: str = Field(default="https://api.upstox.com/v2/option/chain")

    default_api_version: str = Field(default="2.0")
    default_accept_header: str = Field(default="application/json")
    default_content_type: str = Field(default="application/x-www-form-urlencoded")

    # -------------------------------------------------------------------------
    # OAuth & Authentication
    # -------------------------------------------------------------------------
    secret_key: str = Field(default="changeme", description="JWT secret key")
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
    api_retry_attempts: int = Field(default=3)
    api_retry_delay_seconds: int = Field(default=2)
    max_concurrent_requests: int = Field(default=5)
    api_calls_per_minute: int = Field(default=50)
    rate_limit_buffer_seconds: float = Field(default=1.2)
    
    # -------------------------------------------------------------------------
    # Database & Redis
    # -------------------------------------------------------------------------
    database_url: Optional[str] = Field(default=None)
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
    expiry_end_index: Optional[int] = Field(default=None)
    latest_expiry: int = Field(default=5)
    default_trim_hours: int = Field(default=2)
    OUT_BASE_PREFIX: str = Field(default="FINAL", description="Output base folder prefix")
    JOB_REGISTRY_PATH: str = Field(default=".job_registry.json", description="Job registry path (JSON)")
    # -------------------------------------------------------------------------
    # Financial Parameters
    # -------------------------------------------------------------------------
    risk_free_rate: float = Field(default=0.0679)
    periods_per_year: int = Field(default=252)
    rv_window_1min: int = Field(default=390)  # ≈ 1.5 trading days
    rv_window_3min: int = Field(default=130)  # ≈ 1.5 trading days
    rv_buffer_weeks: int = Field(default=2)
    min_rv_coverage: float = Field(default=0.8)
    FALLBACK_BROKERAGE_CHARGE: float=Field(default=82.89)
    # FALLBACK_MARGIN_REQUIREMENT
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


@lru_cache()
def get_settings() -> Settings:
    """Get application settings (cached)."""
    return Settings()


# Module-level settings instance
settings = get_settings()
