# api/exceptions.py
"""
Custom exception classes for robust error handling across the trading platform.
Each service/module should raise the *most precise* subclass for maximal observability & safety.
"""

class APIError(Exception):
    """Base error for all API-related failures."""
    pass

class AuthError(APIError):
    """Raised for authentication or OAuth errors (token expired, not found, etc)."""
    pass

class OAuthCallbackTimeout(AuthError):
    """Raised if the OAuth flow callback did not arrive in time."""
    pass

class TokenRefreshError(AuthError):
    """Raised when token refresh fails after retries."""
    pass

class PermissionDeniedError(AuthError):
    """Raised when the API/server returns permissions/authz failure."""
    pass

class RateLimitError(APIError):
    """Raised when API rate limits are hit or enforced."""
    pass

class DataQualityError(APIError):
    """Raised on missing, incomplete, or structurally-invalid data from an endpoint."""
    pass

class BrokerageCalculationError(APIError):
    """Raised when brokerage calculation API fails or payload is invalid."""
    pass

class MarginCalculationError(APIError):
    """Raised when margin calculation API fails or returns bad payload."""
    pass

class InstrumentLookupError(APIError):
    """Raised for failures in finding instrument metadata, tokens, etc."""
    pass

class NetworkError(APIError):
    """Raised for transport/network failures that are not API bugs (timeout, DNS, etc)."""
    pass

class ConfigError(Exception):
    """Raised for missing configuration, .env, or environment variables."""
    pass

class NotReadyError(Exception):
    """Generalized: raised when a service/engine is not yet ready for use."""
    pass

class ValidationError(Exception):
    """Raised on generic input validation or preconditions (data, parameter checks)."""
    pass
