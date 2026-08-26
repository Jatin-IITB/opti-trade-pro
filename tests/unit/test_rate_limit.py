# tests/unit/test_rate_limit.py
"""Tests for the sliding-window rate limiter."""

import time
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from options_trading.utils.rate_limit import (
    _SlidingWindow,
    rate_limit_login,
    rate_limit_refresh,
)


class TestSlidingWindow:
    def test_allows_within_limit(self):
        window = _SlidingWindow(max_requests=3, window_seconds=60)
        assert window.is_allowed("ip1")
        assert window.is_allowed("ip1")
        assert window.is_allowed("ip1")

    def test_blocks_over_limit(self):
        window = _SlidingWindow(max_requests=2, window_seconds=60)
        assert window.is_allowed("ip1")
        assert window.is_allowed("ip1")
        assert not window.is_allowed("ip1")

    def test_different_keys_independent(self):
        window = _SlidingWindow(max_requests=1, window_seconds=60)
        assert window.is_allowed("ip1")
        assert window.is_allowed("ip2")
        assert not window.is_allowed("ip1")

    def test_window_expires(self):
        window = _SlidingWindow(max_requests=1, window_seconds=0)
        assert window.is_allowed("ip1")
        time.sleep(0.01)
        assert window.is_allowed("ip1")


class TestRateLimitDecorators:
    def _make_request(self, client_host: str = "127.0.0.1"):
        request = MagicMock()
        request.client.host = client_host
        request.headers = {}
        return request

    @pytest.mark.asyncio
    async def test_login_limiter_passes(self):
        @rate_limit_login
        async def handler(request):
            return "ok"

        request = self._make_request("10.0.0.99")
        result = await handler(request=request)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_refresh_limiter_blocks(self):
        @rate_limit_refresh
        async def handler(request):
            return "ok"

        request = self._make_request("10.0.0.100")
        for _ in range(5):
            await handler(request=request)

        with pytest.raises(HTTPException) as exc_info:
            await handler(request=request)
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_forwarded_header_used(self):
        @rate_limit_login
        async def handler(request):
            return "ok"

        request = self._make_request("10.0.0.101")
        request.headers = {"x-forwarded-for": "203.0.113.1, 10.0.0.1"}
        result = await handler(request=request)
        assert result == "ok"
