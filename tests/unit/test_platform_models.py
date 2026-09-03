# tests/unit/test_platform_models.py
"""Tests for options_trading Pydantic models after V2 migration."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from options_trading.models.auth import TokenData
from options_trading.models.dashboard import (
    AuthenticationStatus,
    ConnectionStatus,
    MarketDataFeed,
    MarketDataStatus,
    SystemHealth,
    SystemMetrics,
    SystemStatus,
)
from options_trading.models.market_data import OptionChain, OptionData


class TestTokenData:
    def test_valid_token(self):
        t = TokenData(access_token="abc", expires_in=3600)
        assert t.expires_in == 3600

    def test_rejects_non_positive_expiry(self):
        with pytest.raises(ValueError, match="positive"):
            TokenData(access_token="abc", expires_in=0)

    def test_rejects_negative_expiry(self):
        with pytest.raises(ValueError, match="positive"):
            TokenData(access_token="abc", expires_in=-1)


class TestOptionData:
    def test_valid_option_types(self):
        for ot in ("CE", "PE", "CALL", "PUT"):
            d = OptionData(
                strike=Decimal("20000"),
                option_type=ot,
                last_price=Decimal("100"),
                bid=Decimal("99"),
                ask=Decimal("101"),
            )
            assert d.option_type == ot

    def test_invalid_option_type(self):
        with pytest.raises(ValueError, match="CE, PE, CALL, or PUT"):
            OptionData(
                strike=Decimal("20000"),
                option_type="INVALID",
                last_price=Decimal("100"),
                bid=Decimal("99"),
                ask=Decimal("101"),
            )


class TestOptionChain:
    def test_valid_expiry_date(self):
        chain = OptionChain(
            symbol="NIFTY",
            spot_price=Decimal("20000"),
            expiry_date="2025-01-30",
        )
        assert chain.expiry_date == "2025-01-30"

    def test_invalid_expiry_date(self):
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            OptionChain(
                symbol="NIFTY",
                spot_price=Decimal("20000"),
                expiry_date="30-01-2025",
            )


class TestAuthenticationStatus:
    def test_calculates_time_until_expiry(self):
        future = datetime.now() + timedelta(hours=2, minutes=30)
        status = AuthenticationStatus(
            is_authenticated=True,
            user_id="u1",
            token_expires_at=future,
        )
        assert "2h" in status.time_until_expiry

    def test_expired_token(self):
        past = datetime.now() - timedelta(hours=1)
        status = AuthenticationStatus(
            is_authenticated=True,
            user_id="u1",
            token_expires_at=past,
        )
        assert status.time_until_expiry == "Expired"

    def test_no_token_expiry(self):
        status = AuthenticationStatus(
            is_authenticated=False,
            user_id="u1",
        )
        assert status.time_until_expiry == "Expired"


class TestMarketDataStatus:
    def test_excellent_data_quality(self):
        feeds = [
            MarketDataFeed(
                name="f1",
                status=ConnectionStatus.CONNECTED,
                instruments_count=10,
                error_rate=0.5,
            )
        ]
        status = MarketDataStatus(
            overall_status=ConnectionStatus.CONNECTED,
            feeds_connected=1,
            total_instruments=10,
            feeds=feeds,
        )
        assert status.data_quality == "excellent"

    def test_poor_data_quality(self):
        feeds = [
            MarketDataFeed(
                name="f1",
                status=ConnectionStatus.CONNECTED,
                instruments_count=10,
                error_rate=25.0,
            )
        ]
        status = MarketDataStatus(
            overall_status=ConnectionStatus.CONNECTED,
            feeds_connected=1,
            total_instruments=10,
            feeds=feeds,
        )
        assert status.data_quality == "poor"

    def test_no_feeds_unknown_quality(self):
        status = MarketDataStatus(
            overall_status=ConnectionStatus.DISCONNECTED,
            feeds_connected=0,
            total_instruments=0,
        )
        assert status.data_quality == "unknown"


class TestSystemStatus:
    def _make_auth(self, authenticated: bool = True):
        return AuthenticationStatus(
            is_authenticated=authenticated,
            user_id="u1",
        )

    def _make_market(self, status: ConnectionStatus = ConnectionStatus.CONNECTED):
        return MarketDataStatus(
            overall_status=status,
            feeds_connected=1,
            total_instruments=10,
        )

    def _make_metrics(self, cpu: float = 30, mem: float = 40, errors: int = 0):
        return SystemMetrics(
            uptime="1d",
            cpu_usage_percent=cpu,
            memory_usage_percent=mem,
            disk_usage_percent=20,
            api_response_time_ms=50,
            error_count=errors,
            warning_count=0,
        )

    def test_healthy_system(self):
        s = SystemStatus(
            overall_health=SystemHealth.HEALTHY,
            authentication=self._make_auth(),
            market_data=self._make_market(),
            system_metrics=self._make_metrics(),
        )
        assert s.overall_health == SystemHealth.HEALTHY

    def test_critical_unauthenticated(self):
        s = SystemStatus(
            overall_health=SystemHealth.HEALTHY,
            authentication=self._make_auth(authenticated=False),
            market_data=self._make_market(),
            system_metrics=self._make_metrics(),
        )
        assert s.overall_health == SystemHealth.CRITICAL

    def test_critical_disconnected(self):
        s = SystemStatus(
            overall_health=SystemHealth.HEALTHY,
            authentication=self._make_auth(),
            market_data=self._make_market(status=ConnectionStatus.DISCONNECTED),
            system_metrics=self._make_metrics(),
        )
        assert s.overall_health == SystemHealth.CRITICAL

    def test_warning_high_cpu(self):
        s = SystemStatus(
            overall_health=SystemHealth.HEALTHY,
            authentication=self._make_auth(),
            market_data=self._make_market(),
            system_metrics=self._make_metrics(cpu=75),
        )
        assert s.overall_health == SystemHealth.WARNING

    def test_critical_high_errors(self):
        s = SystemStatus(
            overall_health=SystemHealth.HEALTHY,
            authentication=self._make_auth(),
            market_data=self._make_market(),
            system_metrics=self._make_metrics(errors=15),
        )
        assert s.overall_health == SystemHealth.CRITICAL
