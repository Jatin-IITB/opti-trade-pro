# tests/unit/test_auth_service.py
"""
Unit tests for the AuthService class.
Tests authentication, token management, and OAuth2 flows.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from options_trading.models.auth import OAuthCallbackRequest, TokenInfo
from options_trading.services.auth_service import AuthService
from options_trading.utils.exceptions import AuthError, TokenRefreshError


class TestAuthService:
    """Test suite for AuthService class."""

    @pytest.mark.asyncio
    async def test_get_authorization_url(self, mock_settings):
        """Test OAuth2 authorization URL generation."""
        auth_service = AuthService()

        url = auth_service.get_authorization_url()

        assert "response_type=code" in url
        assert f"client_id={mock_settings.upstox_api_key}" in url
        assert f"redirect_uri={mock_settings.oauth_redirect_uri}" in url
        assert auth_service.oauth_config.authorization_url in url

    @pytest.mark.asyncio
    async def test_get_authorization_url_with_state(self, mock_settings):
        """Test OAuth2 authorization URL generation with state parameter."""
        auth_service = AuthService()
        state = "test_state_123"

        url = auth_service.get_authorization_url(state=state)

        assert f"state={state}" in url

    @pytest.mark.asyncio
    async def test_exchange_code_for_tokens_success(self, mock_settings, mock_secure_storage):
        """Test successful token exchange."""
        auth_service = AuthService(storage=mock_secure_storage)

        # Mock successful HTTP response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "test_access_token",
            "refresh_token": "test_refresh_token",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        mock_response.raise_for_status.return_value = None

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            async with auth_service:
                callback = OAuthCallbackRequest(code="test_code")
                token_info = await auth_service.exchange_code_for_tokens(callback)

            assert token_info.access_token == "test_access_token"
            assert token_info.refresh_token == "test_refresh_token"
            assert token_info.expires_in == 3600

            # Verify token was stored
            mock_secure_storage.store_token.assert_called_once()

    @pytest.mark.asyncio
    async def test_exchange_code_for_tokens_failure(self, mock_settings, mock_secure_storage):
        """Test failed token exchange."""
        auth_service = AuthService(storage=mock_secure_storage)

        # Mock failed HTTP response
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Invalid authorization code"
        mock_response.raise_for_status.side_effect = Exception("HTTP 400")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            async with auth_service:
                callback = OAuthCallbackRequest(code="invalid_code")

                with pytest.raises(AuthError):
                    await auth_service.exchange_code_for_tokens(callback)

    @pytest.mark.asyncio
    async def test_validate_token_success(self, mock_settings, mock_secure_storage):
        """Test successful token validation."""
        auth_service = AuthService(storage=mock_secure_storage)

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            async with auth_service:
                is_valid = await auth_service.validate_token("test_token")

            assert is_valid is True

    @pytest.mark.asyncio
    async def test_validate_token_unauthorized(self, mock_settings, mock_secure_storage):
        """Test token validation with unauthorized response."""
        auth_service = AuthService(storage=mock_secure_storage)

        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            async with auth_service:
                is_valid = await auth_service.validate_token("invalid_token")

            assert is_valid is False

    @pytest.mark.asyncio
    async def test_refresh_token_success(self, mock_settings, mock_secure_storage):
        """Test successful token refresh."""
        auth_service = AuthService(storage=mock_secure_storage)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new_access_token",
            "refresh_token": "new_refresh_token",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        mock_response.raise_for_status.return_value = None

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            async with auth_service:
                token_info = await auth_service.refresh_token("old_refresh_token")

            assert token_info.access_token == "new_access_token"
            assert token_info.refresh_token == "new_refresh_token"

            # Verify new token was stored
            mock_secure_storage.store_token.assert_called_once()

    @pytest.mark.asyncio
    async def test_refresh_token_failure(self, mock_settings, mock_secure_storage):
        """Test failed token refresh."""
        auth_service = AuthService(storage=mock_secure_storage)

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Invalid refresh token"
        mock_response.raise_for_status.side_effect = Exception("HTTP 400")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            async with auth_service:
                with pytest.raises(TokenRefreshError):
                    await auth_service.refresh_token("invalid_refresh_token")

    @pytest.mark.asyncio
    async def test_get_valid_access_token_cached(
        self, mock_settings, mock_secure_storage, sample_token_info
    ):
        """Test getting valid access token from cache."""
        # Mock storage to return valid token
        mock_secure_storage.load_token.return_value = sample_token_info

        auth_service = AuthService(storage=mock_secure_storage)

        # Mock successful token validation
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            async with auth_service:
                access_token = await auth_service.get_valid_access_token()

            assert access_token == sample_token_info.access_token

    @pytest.mark.asyncio
    async def test_get_valid_access_token_refresh_needed(
        self, mock_settings, mock_secure_storage, expired_token_info
    ):
        """Test getting valid access token when refresh is needed."""
        # Mock storage to return expired token
        mock_secure_storage.load_token.return_value = expired_token_info

        auth_service = AuthService(storage=mock_secure_storage)

        # Mock successful token refresh
        refresh_response = MagicMock()
        refresh_response.status_code = 200
        refresh_response.json.return_value = {
            "access_token": "refreshed_access_token",
            "refresh_token": "new_refresh_token",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        refresh_response.raise_for_status.return_value = None

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post.return_value = refresh_response
            mock_client_class.return_value = mock_client

            async with auth_service:
                access_token = await auth_service.get_valid_access_token()

            assert access_token == "refreshed_access_token"

    @pytest.mark.asyncio
    async def test_get_valid_access_token_no_tokens(self, mock_settings, mock_secure_storage):
        """Test getting valid access token when no tokens exist."""
        # Mock storage to return None (no tokens)
        mock_secure_storage.load_token.return_value = None

        auth_service = AuthService(storage=mock_secure_storage)

        async with auth_service:
            with pytest.raises(AuthError, match="Authentication required"):
                await auth_service.get_valid_access_token()

    @pytest.mark.asyncio
    async def test_get_user_profile_success(self, mock_settings, mock_secure_storage):
        """Test successful user profile retrieval."""
        auth_service = AuthService(storage=mock_secure_storage)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "user_id": "test_user_123",
                "user_name": "Test User",
                "email": "test@example.com",
                "mobile": "+91-9876543210",
                "exchanges": ["NSE", "BSE"],
                "products": ["CNC", "MIS", "NRML"],
                "is_active": True,
            }
        }
        mock_response.raise_for_status.return_value = None

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            async with auth_service:
                profile = await auth_service.get_user_profile("test_token")

            assert profile.user_id == "test_user_123"
            assert profile.user_name == "Test User"
            assert profile.email == "test@example.com"
            assert "NSE" in profile.exchanges
            assert profile.is_active is True

    @pytest.mark.asyncio
    async def test_logout(self, mock_settings, mock_secure_storage):
        """Test user logout functionality."""
        auth_service = AuthService(storage=mock_secure_storage)

        await auth_service.logout("test_user")

        mock_secure_storage.clear_token.assert_called_once_with("test_user")

    @pytest.mark.asyncio
    async def test_get_auth_status_authenticated(
        self, mock_settings, mock_secure_storage, sample_token_info
    ):
        """Test auth status when user is authenticated."""
        mock_secure_storage.load_token.return_value = sample_token_info

        auth_service = AuthService(storage=mock_secure_storage)

        status = await auth_service.get_auth_status("test_user")

        assert status.authenticated is True
        assert status.user_id == "test_user"
        assert status.needs_refresh is False

    @pytest.mark.asyncio
    async def test_get_auth_status_expired(
        self, mock_settings, mock_secure_storage, expired_token_info
    ):
        """Test auth status when token is expired."""
        mock_secure_storage.load_token.return_value = expired_token_info

        auth_service = AuthService(storage=mock_secure_storage)

        status = await auth_service.get_auth_status("test_user")

        assert status.authenticated is False
        assert status.user_id == "test_user"
        assert status.needs_refresh is True

    @pytest.mark.asyncio
    async def test_get_auth_status_no_tokens(self, mock_settings, mock_secure_storage):
        """Test auth status when no tokens exist."""
        mock_secure_storage.load_token.return_value = None

        auth_service = AuthService(storage=mock_secure_storage)

        status = await auth_service.get_auth_status("test_user")

        assert status.authenticated is False
        assert status.user_id is None
        assert status.needs_refresh is False


class TestTokenInfo:
    """Test suite for TokenInfo model."""

    def test_token_info_creation(self, sample_token_data):
        """Test TokenInfo object creation."""
        token_info = TokenInfo(**sample_token_data)

        assert token_info.access_token == sample_token_data["access_token"]
        assert token_info.refresh_token == sample_token_data["refresh_token"]
        assert token_info.expires_in == sample_token_data["expires_in"]
        assert token_info.token_type == sample_token_data["token_type"]
        assert isinstance(token_info.created_at, datetime)
        assert isinstance(token_info.expires_at, datetime)

    def test_token_info_expiry_calculation(self, sample_token_data):
        """Test automatic expiry calculation."""
        token_info = TokenInfo(**sample_token_data)

        expected_expiry = token_info.created_at + timedelta(seconds=sample_token_data["expires_in"])
        # Allow small difference due to execution time
        assert abs((token_info.expires_at - expected_expiry).total_seconds()) < 1

    def test_token_info_is_expired_false(self, sample_token_data):
        """Test is_expired property for valid token."""
        token_info = TokenInfo(**sample_token_data)

        assert token_info.is_expired is False

    def test_token_info_is_expired_true(self):
        """Test is_expired property for expired token."""
        past_time = datetime.now() - timedelta(hours=2)
        token_info = TokenInfo(
            access_token="test_token",
            expires_in=3600,
            created_at=past_time,
            expires_at=past_time + timedelta(seconds=3600),
        )

        assert token_info.is_expired is True

    def test_time_until_expiry(self, sample_token_data):
        """Test time_until_expiry property."""
        token_info = TokenInfo(**sample_token_data)

        time_until_expiry = token_info.time_until_expiry
        expected_time = token_info.expires_at - datetime.now()

        # Allow small difference due to execution time
        assert abs((time_until_expiry - expected_time).total_seconds()) < 1
