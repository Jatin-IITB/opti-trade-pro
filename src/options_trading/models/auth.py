# src/options_trading/models/auth.py
"""
Pydantic models for authentication and token management.
Modern replacement for the dict-based token handling in the original code.
"""

from datetime import datetime, timedelta
from typing import Literal, Optional

from pydantic import BaseModel, Field, validator


class TokenData(BaseModel):
    """Token data received from Upstox OAuth2 flow."""
    
    access_token: str = Field(..., description="OAuth2 access token")
    refresh_token: Optional[str] = Field(None, description="OAuth2 refresh token")
    expires_in: int = Field(86400, description="Token expiry in seconds")
    token_type: str = Field("Bearer", description="Token type")
    
    @validator('expires_in')
    def validate_expires_in(cls, v):
        if v <= 0:
            raise ValueError("expires_in must be positive")
        return v


class TokenInfo(BaseModel):
    """Extended token information with metadata."""
    
    access_token: str
    refresh_token: Optional[str] = None
    expires_in: int = 86400
    token_type: str = "Bearer"
    created_at: datetime = Field(default_factory=datetime.now)
    expires_at: datetime
    user_id: str = "default"
    
    def __init__(self, **data):
        if 'expires_at' not in data and 'expires_in' in data:
            data['expires_at'] = datetime.now() + timedelta(seconds=data['expires_in'])
        super().__init__(**data)
    
    @property
    def is_expired(self) -> bool:
        """Check if token is expired with buffer."""
        buffer_time = datetime.now() + timedelta(minutes=5)  # 5-minute buffer
        return self.expires_at <= buffer_time
    
    @property
    def time_until_expiry(self) -> timedelta:
        """Time remaining until token expires."""
        return self.expires_at - datetime.now()


class OAuthConfig(BaseModel):
    """OAuth2 configuration settings."""
    
    client_id: str = Field(..., description="Upstox API key")
    client_secret: str = Field(..., description="Upstox secret key")
    redirect_uri: str = Field(..., description="OAuth2 redirect URI")
    authorization_url: str = Field(
        "https://api-v2.upstox.com/login/authorization/dialog",
        description="OAuth2 authorization URL"
    )
    token_url: str = Field(
        "https://api.upstox.com/v2/login/authorization/token",
        description="Token exchange URL"
    )
    scope: Optional[str] = Field(None, description="OAuth2 scope")


class OAuthCallbackRequest(BaseModel):
    """OAuth2 callback request model."""
    
    code: str = Field(..., description="Authorization code from OAuth2 flow")
    state: Optional[str] = Field(None, description="CSRF protection state parameter")


class OAuthCallbackResponse(BaseModel):
    """OAuth2 callback response model."""
    
    success: bool = Field(..., description="Whether callback was successful")
    message: str = Field(..., description="Status message")
    access_token: Optional[str] = Field(None, description="Access token if successful")


class TokenValidationRequest(BaseModel):
    """Request model for token validation."""
    
    access_token: str = Field(..., description="Token to validate")


class TokenValidationResponse(BaseModel):
    """Response model for token validation."""
    
    valid: bool = Field(..., description="Whether token is valid")
    user_id: Optional[str] = Field(None, description="Associated user ID")
    expires_at: Optional[datetime] = Field(None, description="Token expiry time")
    error: Optional[str] = Field(None, description="Error message if invalid")


class TokenRefreshRequest(BaseModel):
    """Request model for token refresh."""
    
    refresh_token: str = Field(..., description="Refresh token")
    client_id: str = Field(..., description="OAuth2 client ID")
    client_secret: str = Field(..., description="OAuth2 client secret")


class TokenRefreshResponse(BaseModel):
    """Response model for token refresh."""
    
    access_token: str = Field(..., description="New access token")
    refresh_token: Optional[str] = Field(None, description="New refresh token")
    expires_in: int = Field(..., description="Token expiry in seconds")
    token_type: str = Field("Bearer", description="Token type")


class AuthErrorResponse(BaseModel):
    """Error response model for authentication failures."""
    
    error: str = Field(..., description="Error code")
    error_description: str = Field(..., description="Human-readable error description")
    error_uri: Optional[str] = Field(None, description="URI with error information")


class UserProfile(BaseModel):
    """User profile information from Upstox API."""
    
    user_id: str = Field(..., description="Unique user identifier")
    user_name: Optional[str] = Field(None, description="User display name")
    email: Optional[str] = Field(None, description="User email address")
    mobile: Optional[str] = Field(None, description="User mobile number")
    exchanges: list[str] = Field(default_factory=list, description="Enabled exchanges")
    products: list[str] = Field(default_factory=list, description="Enabled products")
    is_active: bool = Field(True, description="Whether user account is active")


class AuthStatus(BaseModel):
    """Current authentication status."""
    
    authenticated: bool = Field(..., description="Whether user is authenticated")
    user_id: Optional[str] = Field(None, description="Current user ID")
    token_expires_at: Optional[datetime] = Field(None, description="Token expiry time")
    needs_refresh: bool = Field(False, description="Whether token needs refresh")
    last_validated: Optional[datetime] = Field(None, description="Last token validation time")