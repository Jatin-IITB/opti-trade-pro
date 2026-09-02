# src/options_trading/utils/exceptions.py
"""
Custom exception classes for the options trading platform.
Provides detailed error types for better error handling and debugging.
"""

from typing import Any


class OptionsTradinError(Exception):
    """Base exception for the options trading platform."""

    def __init__(
        self, message: str, error_code: str | None = None, details: dict[str, Any] | None = None
    ):
        self.message = message
        self.error_code = error_code
        self.details = details or {}
        super().__init__(self.message)

    def __str__(self) -> str:
        if self.error_code:
            return f"[{self.error_code}] {self.message}"
        return self.message


# Authentication Exceptions
class AuthError(OptionsTradinError):
    """Base authentication error."""

    pass


class TokenExpiredError(AuthError):
    """Raised when access token has expired."""

    def __init__(self, message: str = "Access token has expired"):
        super().__init__(message, error_code="TOKEN_EXPIRED")


class TokenRefreshError(AuthError):
    """Raised when token refresh fails."""

    def __init__(self, message: str = "Failed to refresh access token"):
        super().__init__(message, error_code="TOKEN_REFRESH_FAILED")


class OAuthCallbackTimeout(AuthError):
    """Raised when OAuth callback times out."""

    def __init__(self, message: str = "OAuth callback timed out"):
        super().__init__(message, error_code="OAUTH_TIMEOUT")


class InvalidCredentialsError(AuthError):
    """Raised when API credentials are invalid."""

    def __init__(self, message: str = "Invalid API credentials"):
        super().__init__(message, error_code="INVALID_CREDENTIALS")


class PermissionDeniedError(AuthError):
    """Raised when user lacks required permissions."""

    def __init__(self, message: str = "Permission denied"):
        super().__init__(message, error_code="PERMISSION_DENIED")


# API Exceptions
class APIError(OptionsTradinError):
    """Base API error."""

    def __init__(
        self, message: str, status_code: int | None = None, response_data: dict | None = None
    ):
        self.status_code = status_code
        self.response_data = response_data or {}
        super().__init__(
            message,
            error_code="API_ERROR",
            details={"status_code": status_code, "response": response_data},
        )


class RateLimitError(APIError):
    """Raised when API rate limit is exceeded."""

    def __init__(self, message: str = "API rate limit exceeded", retry_after: int | None = None):
        self.retry_after = retry_after
        super().__init__(message)


class NetworkError(APIError):
    """Raised for network-related errors."""

    def __init__(self, message: str = "Network error occurred"):
        super().__init__(message)


class TimeoutError(APIError):
    """Raised when API request times out."""

    def __init__(self, message: str = "Request timed out"):
        super().__init__(message)


# Data Exceptions
class DataError(OptionsTradinError):
    """Base data-related error."""

    pass


class DataQualityError(DataError):
    """Raised when data quality checks fail."""

    def __init__(self, message: str = "Data quality validation failed"):
        super().__init__(message, error_code="DATA_QUALITY_ERROR")


class DataNotFoundError(DataError):
    """Raised when required data is not found."""

    def __init__(self, message: str = "Required data not found"):
        super().__init__(message, error_code="DATA_NOT_FOUND")


class InvalidDataFormatError(DataError):
    """Raised when data format is invalid."""

    def __init__(self, message: str = "Invalid data format"):
        super().__init__(message, error_code="INVALID_DATA_FORMAT")


# Market Data Exceptions
class MarketDataError(DataError):
    """Base market data error."""

    pass


class InstrumentNotFoundError(MarketDataError):
    """Raised when instrument is not found."""

    def __init__(self, symbol: str, exchange: str):
        message = f"Instrument not found: {symbol} on {exchange}"
        super().__init__(message, error_code="INSTRUMENT_NOT_FOUND")
        self.symbol = symbol
        self.exchange = exchange


class ExpiryNotFoundError(MarketDataError):
    """Raised when expiry date is not found."""

    def __init__(self, expiry: str, symbol: str):
        message = f"Expiry {expiry} not found for {symbol}"
        super().__init__(message, error_code="EXPIRY_NOT_FOUND")
        self.expiry = expiry
        self.symbol = symbol


class ContractNotFoundError(MarketDataError):
    """Raised when option contract is not found."""

    def __init__(self, contract_key: str):
        message = f"Option contract not found: {contract_key}"
        super().__init__(message, error_code="CONTRACT_NOT_FOUND")
        self.contract_key = contract_key


# Calculation Exceptions
class CalculationError(OptionsTradinError):
    """Base calculation error."""

    pass


class BlackScholesError(CalculationError):
    """Raised when Black-Scholes calculation fails."""

    def __init__(self, message: str = "Black-Scholes calculation failed"):
        super().__init__(message, error_code="BLACK_SCHOLES_ERROR")


class ImpliedVolatilityError(CalculationError):
    """Raised when implied volatility calculation fails."""

    def __init__(self, message: str = "Implied volatility calculation failed"):
        super().__init__(message, error_code="IMPLIED_VOLATILITY_ERROR")


class GreeksCalculationError(CalculationError):
    """Raised when Greeks calculation fails."""

    def __init__(self, message: str = "Greeks calculation failed"):
        super().__init__(message, error_code="GREEKS_CALCULATION_ERROR")


class RealizedVolatilityError(CalculationError):
    """Raised when realized volatility calculation fails."""

    def __init__(self, message: str = "Realized volatility calculation failed"):
        super().__init__(message, error_code="REALIZED_VOLATILITY_ERROR")


# Trading Exceptions
class TradingError(OptionsTradinError):
    """Base trading error."""

    pass


class BrokerageCalculationError(TradingError):
    """Raised when brokerage calculation fails."""

    def __init__(self, message: str = "Brokerage calculation failed"):
        super().__init__(message, error_code="BROKERAGE_CALCULATION_ERROR")


class MarginCalculationError(TradingError):
    """Raised when margin calculation fails."""

    def __init__(self, message: str = "Margin calculation failed"):
        super().__init__(message, error_code="MARGIN_CALCULATION_ERROR")


class InsufficientFundsError(TradingError):
    """Raised when account has insufficient funds."""

    def __init__(self, required: float, available: float):
        message = f"Insufficient funds: required {required}, available {available}"
        super().__init__(message, error_code="INSUFFICIENT_FUNDS")
        self.required = required
        self.available = available


# Configuration Exceptions
class ConfigError(OptionsTradinError):
    """Base configuration error."""

    pass


class MissingConfigError(ConfigError):
    """Raised when required configuration is missing."""

    def __init__(self, config_name: str):
        message = f"Missing required configuration: {config_name}"
        super().__init__(message, error_code="MISSING_CONFIG")
        self.config_name = config_name


class InvalidConfigError(ConfigError):
    """Raised when configuration value is invalid."""

    def __init__(self, config_name: str, value: Any, reason: str):
        message = f"Invalid configuration {config_name}={value}: {reason}"
        super().__init__(message, error_code="INVALID_CONFIG")
        self.config_name = config_name
        self.value = value
        self.reason = reason


# Storage Exceptions
class StorageError(OptionsTradinError):
    """Base storage error."""

    pass


class TokenStorageError(StorageError):
    """Raised when token storage operations fail."""

    def __init__(self, message: str = "Token storage operation failed"):
        super().__init__(message, error_code="TOKEN_STORAGE_ERROR")


class CacheError(StorageError):
    """Raised when cache operations fail."""

    def __init__(self, message: str = "Cache operation failed"):
        super().__init__(message, error_code="CACHE_ERROR")


# Validation Exceptions
class ValidationError(OptionsTradinError):
    """Base validation error."""

    pass


class ParameterValidationError(ValidationError):
    """Raised when parameter validation fails."""

    def __init__(self, parameter: str, value: Any, reason: str):
        message = f"Invalid parameter {parameter}={value}: {reason}"
        super().__init__(message, error_code="PARAMETER_VALIDATION_ERROR")
        self.parameter = parameter
        self.value = value
        self.reason = reason


class DataValidationError(ValidationError):
    """Raised when data validation fails."""

    def __init__(self, message: str = "Data validation failed"):
        super().__init__(message, error_code="DATA_VALIDATION_ERROR")


# Service Exceptions
class ServiceError(OptionsTradinError):
    """Base service error."""

    pass


class ServiceUnavailableError(ServiceError):
    """Raised when a service is unavailable."""

    def __init__(self, service_name: str):
        message = f"Service unavailable: {service_name}"
        super().__init__(message, error_code="SERVICE_UNAVAILABLE")
        self.service_name = service_name


class ServiceTimeoutError(ServiceError):
    """Raised when a service times out."""

    def __init__(self, service_name: str, timeout: float):
        message = f"Service timeout: {service_name} ({timeout}s)"
        super().__init__(message, error_code="SERVICE_TIMEOUT")
        self.service_name = service_name
        self.timeout = timeout
