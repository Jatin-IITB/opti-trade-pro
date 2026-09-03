"""Tests for the market-data REST routes' timestamps and cache-stats payload.

Two of these endpoints were unconditionally broken: ``/refresh`` and
``/cache/stats`` called ``datetime.utcnow()`` while the module only does
``import datetime``, so every request raised ``AttributeError`` and returned a
500. The remaining timestamps used the deprecated ``datetime.utcnow()``, which
returns a *naive* value, and then suffixed ``"Z"`` (or, on the historical
processing route, a lowercase ``"z"``) as though it were UTC-aware.

These tests assert the contract rather than a clock reading, so they carry no
wall-clock dependence: a timestamp must parse, must be timezone-aware, and must
be at zero offset.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from options_trading.api.dependencies import (
    get_market_data_manager,
    get_market_data_service,
)
from options_trading.api.routes.market_data import router as market_data_router
from options_trading.utils.cache import MarketDataCache


def assert_utc_aware(timestamp: str) -> None:
    """A timestamp must be self-describing, not naive-plus-a-'Z'."""
    # A trailing "Z"/"z" is what the old code appended by hand; isoformat() on
    # an aware value emits a real "+00:00" offset instead.
    assert not timestamp.endswith(("Z", "z"))
    parsed = dt.datetime.fromisoformat(timestamp)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == dt.timedelta(0)


@pytest.fixture()
def manager() -> MagicMock:
    mgr = MagicMock()
    mgr.get_underlying_key.return_value = "NSE_INDEX|Nifty 50"
    mgr.fetch_contracts_for_expiry.return_value = pd.DataFrame()
    # A real cache so cache_stats() returns the real shape, not a mock's.
    mgr.spot_cache = MarketDataCache(ttl=30, max_size=64)
    mgr.cache_stats.side_effect = lambda: mgr.spot_cache.get_stats()
    return mgr


@pytest.fixture()
def service() -> MagicMock:
    svc = MagicMock()
    svc.refresh_market_data = AsyncMock(return_value=None)
    return svc


@pytest.fixture()
def client(manager: MagicMock, service: MagicMock) -> TestClient:
    app = FastAPI()
    app.include_router(market_data_router, prefix="/api/v1")
    app.dependency_overrides[get_market_data_manager] = lambda: manager
    app.dependency_overrides[get_market_data_service] = lambda: service
    return TestClient(app)


class TestRefreshEndpoint:
    """``POST /market-data/refresh`` — previously an AttributeError 500."""

    def test_returns_200(self, client: TestClient) -> None:
        resp = client.post("/api/v1/market-data/refresh")
        assert resp.status_code == 200
        assert resp.json()["message"] == "Market data refresh initiated"

    def test_timestamp_is_utc_aware(self, client: TestClient) -> None:
        resp = client.post("/api/v1/market-data/refresh")
        assert_utc_aware(resp.json()["timestamp"])

    def test_schedules_the_real_refresh(self, client: TestClient, service: MagicMock) -> None:
        client.post("/api/v1/market-data/refresh")
        service.refresh_market_data.assert_awaited_once()


class TestCacheStatsEndpoint:
    """``GET /market-data/cache/stats`` — previously an AttributeError 500.

    It also discarded the value returned by ``cache_stats()`` and reported a
    fixed ``cache_size`` of 10 regardless of the cache's real contents.
    """

    def test_reports_the_real_stats(self, client: TestClient, manager: MagicMock) -> None:
        resp = client.get("/api/v1/market-data/cache/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["cache_type"] == "MarketDataCache"
        assert body["stats"] == manager.spot_cache.get_stats()
        assert_utc_aware(body["timestamp"])

    def test_max_size_tracks_the_cache_not_a_constant(self, client: TestClient) -> None:
        # The fixture builds a 64-entry cache; the old payload said 10.
        stats = client.get("/api/v1/market-data/cache/stats").json()["stats"]
        assert stats["max_size"] == 64
        assert stats["total_items"] == 0

    def test_503_when_stats_unavailable(self, client: TestClient, manager: MagicMock) -> None:
        # cache_stats() swallows its own errors and returns {}; that must be
        # reported as unavailable rather than dressed up as a real reading.
        manager.cache_stats.side_effect = None
        manager.cache_stats.return_value = {}
        assert client.get("/api/v1/market-data/cache/stats").status_code == 503


class TestInstrumentTimestamps:
    def test_instruments_timestamp_is_utc_aware(self, client: TestClient) -> None:
        resp = client.get("/api/v1/market-data/instruments/NIFTY")
        assert resp.status_code == 200
        assert_utc_aware(resp.json()["timestamp"])

    def test_empty_contracts_timestamp_is_utc_aware(self, client: TestClient) -> None:
        resp = client.get(
            "/api/v1/market-data/contracts/NIFTY", params={"expiry_date": "2026-09-24"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        assert_utc_aware(body["timestamp"])


class TestNoNaiveUtcnowRemains:
    """Guards the deprecation and the naive-with-'Z' pattern together.

    ``datetime.utcnow()`` is deprecated in 3.12+ and returns a naive value.
    Any reintroduction here re-creates both the deprecation warning and the
    mislabelled-offset bug, so it is cheaper to pin the source.
    """

    def test_module_source_has_no_utcnow(self) -> None:
        from pathlib import Path

        from options_trading.api.routes import market_data

        source = Path(market_data.__file__).read_text(encoding="utf-8")
        assert "utcnow" not in source
