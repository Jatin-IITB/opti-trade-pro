# tests/conftest.py
"""
Pytest configuration and fixtures for the options trading platform.
Provides common test fixtures and setup for unit and integration tests.
"""

import asyncio
import json
import os
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import pandas as pd
import pytest
import pytest_asyncio

# Settings instantiate at import inside options_trading; give the required
# fields harmless defaults so the suite runs without a .env (CI included).
os.environ.setdefault("UPSTOX_API_KEY", "test_api_key")
os.environ.setdefault("UPSTOX_SECRET_KEY", "test_secret_key")

from options_trading.models.auth import TokenInfo
from options_trading.services.auth_service import AuthService


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def test_settings():
    """Provide test configuration settings."""
    return {
        "upstox_api_key": "test_api_key",
        "upstox_secret_key": "test_secret_key",
        "upstox_base_url": "https://api.upstox.com",
        "oauth_redirect_uri": "http://localhost:8000/auth/callback",
        "database_url": "sqlite:///:memory:",
        "redis_url": "redis://localhost:6379/1",
        "log_level": "DEBUG",
        "environment": "testing",
    }


@pytest.fixture
def mock_settings(test_settings, monkeypatch):
    """Mock the settings for tests."""
    settings_mock = MagicMock()
    for key, value in test_settings.items():
        setattr(settings_mock, key, value)

    def mock_get_settings():
        return settings_mock

    # AuthService binds get_settings at import time, so patch the name in the
    # consuming module, not only the defining one.
    monkeypatch.setattr("options_trading.config.settings.get_settings", mock_get_settings)
    monkeypatch.setattr("options_trading.services.auth_service.get_settings", mock_get_settings)
    return settings_mock


@pytest.fixture
def sample_token_data():
    """Provide sample token data for tests."""
    return {
        "access_token": "test_access_token_12345",
        "refresh_token": "test_refresh_token_67890",
        "expires_in": 3600,
        "token_type": "Bearer",
    }


@pytest.fixture
def sample_token_info(sample_token_data):
    """Provide sample TokenInfo object for tests."""
    return TokenInfo(
        access_token=sample_token_data["access_token"],
        refresh_token=sample_token_data["refresh_token"],
        expires_in=sample_token_data["expires_in"],
        token_type=sample_token_data["token_type"],
        created_at=datetime.now(),
        user_id="test_user",
    )


@pytest.fixture
def expired_token_info(sample_token_data):
    """Provide expired TokenInfo object for tests."""
    past_time = datetime.now() - timedelta(hours=2)
    return TokenInfo(
        access_token=sample_token_data["access_token"],
        refresh_token=sample_token_data["refresh_token"],
        expires_in=3600,
        token_type=sample_token_data["token_type"],
        created_at=past_time,
        expires_at=past_time + timedelta(seconds=3600),
        user_id="test_user",
    )


@pytest.fixture
def mock_httpx_client():
    """Mock httpx.AsyncClient for API calls."""
    client_mock = AsyncMock(spec=httpx.AsyncClient)

    # Mock successful token exchange
    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json.return_value = {
        "access_token": "test_access_token",
        "refresh_token": "test_refresh_token",
        "expires_in": 3600,
        "token_type": "Bearer",
    }
    token_response.raise_for_status.return_value = None

    # Mock successful profile fetch
    profile_response = MagicMock()
    profile_response.status_code = 200
    profile_response.json.return_value = {
        "data": {
            "user_id": "test_user",
            "user_name": "Test User",
            "email": "test@example.com",
            "exchanges": ["NSE"],
            "products": ["CNC", "MIS"],
            "is_active": True,
        }
    }
    profile_response.raise_for_status.return_value = None

    client_mock.post.return_value = token_response
    client_mock.get.return_value = profile_response

    return client_mock


@pytest.fixture
def mock_secure_storage():
    """Mock secure storage for token management."""
    storage_mock = AsyncMock()

    # Mock token storage and retrieval (SecureStorage methods are all async)
    storage_mock.store_token = AsyncMock()
    storage_mock.load_token = AsyncMock(return_value=None)
    storage_mock.clear_token = AsyncMock()
    storage_mock.list_stored_users = AsyncMock(return_value=[])
    storage_mock.cleanup_expired_tokens = AsyncMock(return_value=0)

    return storage_mock


@pytest_asyncio.fixture
async def auth_service(mock_settings, mock_secure_storage):
    """Provide configured AuthService for tests."""
    service = AuthService(storage=mock_secure_storage)
    async with service:
        yield service


@pytest.fixture
def sample_option_data():
    """Provide sample option OHLCV data for tests."""
    dates = pd.date_range("2024-01-15 09:15:00", "2024-01-15 15:30:00", freq="3min")
    return pd.DataFrame(
        {
            "timestamp": dates,
            "open": [100 + i * 0.1 for i in range(len(dates))],
            "high": [101 + i * 0.1 for i in range(len(dates))],
            "low": [99 + i * 0.1 for i in range(len(dates))],
            "close": [100.5 + i * 0.1 for i in range(len(dates))],
            "volume": [1000 + i * 10 for i in range(len(dates))],
            "open_interest": [5000 + i * 5 for i in range(len(dates))],
        }
    )


@pytest.fixture
def sample_spot_data():
    """Provide sample spot OHLCV data for tests."""
    dates = pd.date_range("2024-01-15 09:15:00", "2024-01-15 15:30:00", freq="3min")
    return pd.DataFrame(
        {
            "timestamp": dates,
            "open": [22000 + i * 1 for i in range(len(dates))],
            "high": [22050 + i * 1 for i in range(len(dates))],
            "low": [21950 + i * 1 for i in range(len(dates))],
            "close": [22025 + i * 1 for i in range(len(dates))],
            "volume": [100000 + i * 100 for i in range(len(dates))],
            "oi": [500000 + i * 50 for i in range(len(dates))],
        }
    )


@pytest.fixture
def sample_option_contracts():
    """Provide sample option contracts data for tests."""
    return pd.DataFrame(
        {
            "instrument_key": [
                "NSE_INDEX|99926000",
                "NSE_INDEX|99926001",
                "NSE_INDEX|99926002",
                "NSE_INDEX|99926003",
            ],
            "trading_symbol": [
                "NIFTY24JAN22000CE",
                "NIFTY24JAN22000PE",
                "NIFTY24JAN22100CE",
                "NIFTY24JAN22100PE",
            ],
            "expiry": [pd.Timestamp("2024-01-25")] * 4,
            "strike_price": [22000.0, 22000.0, 22100.0, 22100.0],
            "instrument_type": ["CE", "PE", "CE", "PE"],
            "underlying_key": ["NSE_INDEX|99926261"] * 4,
            "weekly": [True] * 4,
        }
    ).set_index("instrument_key")


@pytest.fixture
def mock_upstox_api_responses():
    """Mock responses for various Upstox API endpoints."""
    return {
        "token_exchange": {
            "status_code": 200,
            "json": {
                "access_token": "test_access_token",
                "refresh_token": "test_refresh_token",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        },
        "profile": {
            "status_code": 200,
            "json": {
                "status": "success",
                "data": {
                    "user_id": "test_user",
                    "user_name": "Test User",
                    "email": "test@example.com",
                    "exchanges": ["NSE"],
                    "products": ["CNC", "MIS"],
                    "is_active": True,
                },
            },
        },
        "expiries": {
            "status_code": 200,
            "json": {"status": "success", "data": ["2024-01-25", "2024-02-29", "2024-03-28"]},
        },
        "contracts": {
            "status_code": 200,
            "json": {
                "status": "success",
                "data": [
                    {
                        "instrument_key": "NSE_INDEX|99926000",
                        "trading_symbol": "NIFTY24JAN22000CE",
                        "expiry": "2024-01-25",
                        "strike_price": 22000.0,
                        "instrument_type": "CE",
                        "underlying_key": "NSE_INDEX|99926261",
                        "weekly": True,
                    }
                ],
            },
        },
        "option_candles": {
            "status_code": 200,
            "json": {
                "status": "success",
                "data": {
                    "candles": [
                        ["2024-01-15T09:15:00+05:30", 100.0, 101.0, 99.0, 100.5, 1000, 5000],
                        ["2024-01-15T09:18:00+05:30", 100.5, 101.5, 99.5, 101.0, 1100, 5100],
                    ]
                },
            },
        },
        "spot_candles": {
            "status_code": 200,
            "json": {
                "status": "success",
                "data": {
                    "candles": [
                        [
                            "2024-01-15T09:15:00+05:30",
                            22000.0,
                            22050.0,
                            21950.0,
                            22025.0,
                            100000,
                            500000,
                        ],
                        [
                            "2024-01-15T09:18:00+05:30",
                            22025.0,
                            22075.0,
                            21975.0,
                            22050.0,
                            110000,
                            510000,
                        ],
                    ]
                },
            },
        },
    }


@pytest.fixture
def black_scholes_test_cases():
    """Provide test cases for Black-Scholes calculations."""
    return [
        {
            "name": "ATM Call",
            "spot": 22000.0,
            "strike": 22000.0,
            "time_to_expiry": 0.0274,  # 10 days
            "risk_free_rate": 0.0679,
            "volatility": 0.20,
            "option_type": "CE",
            "expected_price_range": (150, 250),
            "expected_delta_range": (0.4, 0.6),
            "expected_gamma_range": (0.00005, 0.0002),
        },
        {
            "name": "ITM Put",
            "spot": 21900.0,
            "strike": 22000.0,
            "time_to_expiry": 0.0274,
            "risk_free_rate": 0.0679,
            "volatility": 0.25,
            "option_type": "PE",
            "expected_price_range": (200, 400),
            "expected_delta_range": (-0.7, -0.4),
            "expected_gamma_range": (0.00005, 0.0002),
        },
    ]


@pytest.fixture
def test_database_url():
    """Provide test database URL."""
    return "sqlite:///:memory:"


@pytest.fixture
def temp_data_dir(tmp_path):
    """Provide temporary directory for test data files."""
    data_dir = tmp_path / "test_data"
    data_dir.mkdir()
    return data_dir


class MockResponse:
    """Mock HTTP response for testing."""

    def __init__(self, json_data: dict, status_code: int = 200):
        self.json_data = json_data
        self.status_code = status_code
        self.text = json.dumps(json_data)

    def json(self):
        return self.json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                message=f"HTTP {self.status_code}", request=MagicMock(), response=self
            )


@pytest.fixture
def mock_requests_session():
    """Mock requests session for HTTP calls."""
    session_mock = MagicMock()

    def mock_request(method, url, **kwargs):
        # Return appropriate mock response based on URL
        if "token" in url:
            return MockResponse(
                {
                    "access_token": "test_token",
                    "refresh_token": "test_refresh",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                }
            )
        elif "profile" in url:
            return MockResponse({"status": "success", "data": {"user_id": "test_user"}})
        else:
            return MockResponse({"status": "success", "data": {}})

    session_mock.request = mock_request
    session_mock.get = lambda url, **kwargs: mock_request("GET", url, **kwargs)
    session_mock.post = lambda url, **kwargs: mock_request("POST", url, **kwargs)

    return session_mock
