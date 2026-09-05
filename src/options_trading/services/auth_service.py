# src/options_trading/services/auth_service.py

"""
FIXED: Modern authentication service using FastAPI and async patterns.
Fixed user ID handling and token storage issues.
"""

import logging
import ssl
import uuid

import httpx
import truststore

from ..config.settings import get_settings
from ..models.auth import (
    AuthStatus,
    OAuthCallbackRequest,
    OAuthConfig,
    TokenData,
    TokenInfo,
    TokenRefreshRequest,
    UserProfile,
)
from ..utils.exceptions import AuthError, TokenRefreshError
from ..utils.security import SecureStorage

logger = logging.getLogger(__name__)


def _system_ssl_context() -> ssl.SSLContext:
    """Build an SSL context that trusts the OS certificate store (handles corporate proxies)."""
    ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return ctx


class AuthService:
    """
    FIXED: Modern authentication service with proper user ID handling.
    """

    def __init__(self, storage: SecureStorage | None = None):
        self.settings = get_settings()
        self.storage = storage or SecureStorage()
        self.oauth_config = OAuthConfig(
            client_id=self.settings.upstox_api_key,
            client_secret=self.settings.upstox_secret_key,
            redirect_uri=self.settings.oauth_redirect_uri,
        )
        self._http_client: httpx.AsyncClient | None = None

    async def load_connector_credentials(self) -> None:
        """Override OAuth config from connector store if configured via UI."""
        from .connector_store import ConnectorStore

        store = ConnectorStore()
        config = await store.get_config("upstox")
        if config and config.get("api_key") and config.get("api_secret"):
            self.oauth_config = OAuthConfig(
                client_id=config["api_key"],
                client_secret=config["api_secret"],
                redirect_uri=config.get("redirect_uri", self.settings.oauth_redirect_uri),
            )
            logger.info("OAuth config loaded from connector store (UI-configured)")

    async def __aenter__(self):
        """Async context manager entry."""
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            verify=_system_ssl_context(),
            headers={
                "Accept": "application/json",
                "Api-Version": "2.0",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self._http_client:
            await self._http_client.aclose()

    def get_authorization_url(self, state: str | None = None) -> str:
        """Generate OAuth2 authorization URL."""
        params = {
            "response_type": "code",
            "client_id": self.oauth_config.client_id,
            "redirect_uri": self.oauth_config.redirect_uri,
        }
        if state:
            params["state"] = state

        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{self.oauth_config.authorization_url}?{query_string}"

    async def exchange_code_for_tokens(
        self, callback: OAuthCallbackRequest, initial_user_id: str = "default"
    ) -> TokenInfo:
        """
        FIXED: Exchange authorization code for access tokens with proper user ID handling.
        """
        if not self._http_client:
            raise RuntimeError("AuthService must be used as async context manager")

        data = {
            "grant_type": "authorization_code",
            "code": callback.code,
            "client_id": self.oauth_config.client_id,
            "client_secret": self.oauth_config.client_secret,
            "redirect_uri": self.oauth_config.redirect_uri,
        }

        try:
            response = await self._http_client.post(self.oauth_config.token_url, data=data)
            response.raise_for_status()
            token_data = TokenData(**response.json())

            # FIXED: Create temporary token to fetch user profile
            temp_token_info = TokenInfo(
                access_token=token_data.access_token,
                refresh_token=token_data.refresh_token,
                expires_in=token_data.expires_in,
                token_type=token_data.token_type,
                user_id=initial_user_id or "temp",  # Temporary user_id
            )

            # Fetch actual user profile to get real user_id
            try:
                user_profile = await self.get_user_profile(temp_token_info.access_token)
                actual_user_id = user_profile.user_id
                logger.info(f"Fetched user profile: {actual_user_id}")
            except Exception as e:
                logger.error(f"Failed to fetch user profile: {e}")
                # Generate a unique fallback user_id
                actual_user_id = f"user_{str(uuid.uuid4())[:8]}"
                logger.warning(f"Using generated user_id: {actual_user_id}")

            # FIXED: Create final token with real user_id
            token_info = TokenInfo(
                access_token=token_data.access_token,
                refresh_token=token_data.refresh_token,
                expires_in=token_data.expires_in,
                token_type=token_data.token_type,
                user_id=actual_user_id,  # Use actual user_id from profile
            )

            # Store token securely
            await self.storage.store_token(token_info)
            logger.info(
                f"Successfully exchanged authorization code for tokens for user: {actual_user_id}"
            )

            return token_info

        except httpx.HTTPStatusError as e:
            logger.error(f"Token exchange failed: {e.response.status_code} - {e.response.text}")
            raise AuthError(f"Failed to exchange authorization code: {e.response.text}")
        except Exception as e:
            logger.error(f"Unexpected error during token exchange: {e}")
            raise AuthError(f"Token exchange failed: {e!s}")

    async def get_valid_access_token(self, user_id: str = "default") -> str:
        """Get a valid access token, refreshing if necessary."""
        # Try to load existing token
        token_info = await self.storage.load_token(user_id)

        if not token_info and user_id == "default":
            try:
                stored_users = await self.storage.list_stored_users()
                if stored_users:
                    fallback_user = stored_users[0]
                    token_info = await self.storage.load_token(fallback_user)
                    logger.info(f"Using fallback user token: {fallback_user}")
            except Exception as e:
                logger.warning(f"Failed to load fallback user token: {e}")

        if token_info:
            # Check if token is still valid
            if not token_info.is_expired and await self.validate_token(token_info.access_token):
                return token_info.access_token

            # Try to refresh token
            if token_info.refresh_token:
                try:
                    new_token = await self.refresh_token(
                        token_info.refresh_token, token_info.user_id
                    )
                    logger.info("Successfully refreshed access token")
                    return new_token.access_token
                except TokenRefreshError:
                    logger.warning("Token refresh failed, need fresh authentication")
                    pass

        # Need fresh authentication
        logger.info("No valid tokens found, fresh authentication required")
        raise AuthError("Authentication required - no valid tokens available")

    # FIXED: Add the missing get_access_token method
    async def get_access_token(self, user_id: str = "default") -> str:
        """
        FIXED: Get access token (alias for get_valid_access_token for compatibility)
        """
        return await self.get_valid_access_token(user_id)

    async def refresh_token(self, refresh_token: str, user_id: str = "default") -> TokenInfo:
        """Refresh access token using refresh token."""
        if not self._http_client:
            raise RuntimeError("AuthService must be used as async context manager")

        request_data = TokenRefreshRequest(
            refresh_token=refresh_token,
            client_id=self.oauth_config.client_id,
            client_secret=self.oauth_config.client_secret,
        )

        data = {
            "grant_type": "refresh_token",
            "refresh_token": request_data.refresh_token,
            "client_id": request_data.client_id,
            "client_secret": request_data.client_secret,
        }

        try:
            response = await self._http_client.post(self.oauth_config.token_url, data=data)
            response.raise_for_status()
            token_data = TokenData(**response.json())

            token_info = TokenInfo(
                access_token=token_data.access_token,
                refresh_token=token_data.refresh_token,
                expires_in=token_data.expires_in,
                token_type=token_data.token_type,
                user_id=user_id,
            )

            # Store new token
            await self.storage.store_token(token_info)
            logger.info("Successfully refreshed token")
            return token_info

        except httpx.HTTPStatusError as e:
            logger.error(f"Token refresh failed: {e.response.status_code} - {e.response.text}")
            raise TokenRefreshError(f"Failed to refresh token: {e.response.text}")
        except Exception as e:
            logger.error(f"Unexpected error during token refresh: {e}")
            raise TokenRefreshError(f"Token refresh failed: {e!s}")

    async def validate_token(self, access_token: str) -> bool:
        """Validate access token with Upstox API."""
        if not self._http_client:
            raise RuntimeError("AuthService must be used as async context manager")

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Api-Version": "2.0",
        }

        try:
            response = await self._http_client.get(
                f"{self.settings.upstox_base_url}/v2/user/profile", headers=headers
            )

            if response.status_code == 200:
                logger.debug("Token validation successful")
                return True
            elif response.status_code == 401:
                logger.debug("Token validation failed - unauthorized")
                return False
            else:
                logger.warning(f"Token validation inconclusive: {response.status_code}")
                return False

        except Exception as e:
            logger.error(f"Token validation error: {e}")
            return False

    async def get_user_profile(self, access_token: str) -> UserProfile:
        """Get user profile information."""
        if not self._http_client:
            raise RuntimeError("AuthService must be used as async context manager")

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Api-Version": "2.0",
        }

        try:
            response = await self._http_client.get(
                f"{self.settings.upstox_base_url}/v2/user/profile", headers=headers
            )
            response.raise_for_status()

            return UserProfile(**response.json().get("data", {}))

        except httpx.HTTPStatusError as e:
            raise AuthError(f"Failed to fetch user profile: {e.response.text}")
        except Exception as e:
            raise AuthError(f"Profile fetch failed: {e!s}")

    async def logout(self, user_id: str = "default") -> None:
        """Logout user by clearing stored tokens."""
        await self.storage.clear_token(user_id)
        logger.info(f"Cleared tokens for user: {user_id}")

    async def get_auth_status(self, user_id: str | None = "default") -> AuthStatus:
        """Get current authentication status."""
        token_info = await self.storage.load_token(user_id)

        if not token_info and user_id == "default":
            try:
                stored_users = await self.storage.list_stored_users()
                if stored_users:
                    fallback_user = stored_users[0]
                    token_info = await self.storage.load_token(fallback_user)
                    logger.debug(f"Using fallback user token: {fallback_user}")
            except Exception as e:
                logger.warning(f"Failed to load fallback user token: {e}")

        if not token_info:
            return AuthStatus(authenticated=False)

        return AuthStatus(
            authenticated=not token_info.is_expired,
            user_id=token_info.user_id,
            token_expires_at=token_info.expires_at,
            needs_refresh=token_info.is_expired and token_info.refresh_token is not None,
        )


# FIXED: Convenience function for backward compatibility
async def get_access_token_automated(user_id: str = "default") -> str:
    async with AuthService() as auth_service:
        return await auth_service.get_valid_access_token(user_id)
