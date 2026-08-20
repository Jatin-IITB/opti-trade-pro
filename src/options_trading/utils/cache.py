# src/options_trading/utils/cache.py
"""
Advanced async caching utilities for high-performance trading platform.
Provides an async key/value cache with TTL and simple LRU eviction,
and a domain-specific MarketDataCache with convenience methods.
"""

import asyncio
import logging
import re
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class AsyncCache:
    """
    High-performance async cache with TTL support.
    - ttl: default TTL in seconds
    - max_size: max number of entries before eviction
    """

    def __init__(self, ttl: int = 300, max_size: int = 1000):
        self.ttl = ttl
        self.max_size = max_size
        # key -> dict(value=..., expires_at=..., created_at=..., last_accessed=..., access_count=...)
        self._cache: dict[str, dict[str, Any]] = {}
        self._cleanup_task: asyncio.Task | None = None
        # ensure there's only one cleanup task per process
        self._cleanup_lock = asyncio.Lock()

    async def get_by_key(self, key: str) -> Any | None:
        """Async get by exact string key"""
        try:
            item = self._cache.get(key)
            if not item:
                return None
            if time.time() < item["expires_at"]:
                item["access_count"] = item.get("access_count", 0) + 1
                item["last_accessed"] = time.time()
                return item["value"]
            else:
                # expired
                del self._cache[key]
                return None
        except Exception as e:
            logger.error(f"Cache get_by_key error for key={key}: {e}")
            return None

    async def set_by_key(self, key: str, value: Any, ttl: int | None = None) -> bool:
        """Async set by key"""
        try:
            effective_ttl = ttl if ttl is not None else self.ttl
            expires_at = time.time() + effective_ttl

            # evict if needed
            if len(self._cache) >= self.max_size and key not in self._cache:
                await self._evict_lru()

            self._cache[key] = {
                "value": value,
                "expires_at": expires_at,
                "created_at": time.time(),
                "last_accessed": time.time(),
                "access_count": 0,
            }

            # start cleanup if not running
            async with self._cleanup_lock:
                if self._cleanup_task is None or self._cleanup_task.done():
                    self._cleanup_task = asyncio.create_task(self._periodic_cleanup())

            return True
        except Exception as e:
            logger.error(f"Cache set_by_key error for key={key}: {e}")
            return False

    async def delete(self, key: str) -> bool:
        try:
            if key in self._cache:
                del self._cache[key]
                return True
            return False
        except Exception as e:
            logger.error(f"Cache delete error for key {key}: {e}")
            return False

    async def delete_pattern(self, pattern: str) -> int:
        try:
            # pattern like "*SYMBOL*"
            regex = re.compile("^" + re.escape(pattern).replace(r"\*", ".*") + "$")
            keys_to_delete = [k for k in list(self._cache.keys()) if regex.match(k)]
            for k in keys_to_delete:
                del self._cache[k]
            return len(keys_to_delete)
        except Exception as e:
            logger.error(f"Cache delete_pattern error for pattern {pattern}: {e}")
            return 0

    async def clear(self) -> None:
        try:
            self._cache.clear()
        except Exception as e:
            logger.error(f"Cache clear error: {e}")

    async def exists(self, key: str) -> bool:
        return (await self.get_by_key(key)) is not None

    async def _evict_lru(self) -> None:
        if not self._cache:
            return
        # LRU by last_accessed (smallest)
        lru_key = min(self._cache.keys(), key=lambda k: self._cache[k]["last_accessed"])
        del self._cache[lru_key]
        logger.debug(f"Evicted LRU cache item: {lru_key}")

    async def _periodic_cleanup(self) -> None:
        """Cleanup expired items periodically"""
        try:
            while True:
                await asyncio.sleep(60)
                now = time.time()
                expired = [k for k, v in list(self._cache.items()) if now >= v["expires_at"]]
                for k in expired:
                    del self._cache[k]
                if expired:
                    logger.debug(f"AsyncCache cleanup removed {len(expired)} items")
        except asyncio.CancelledError:
            # shutting down
            return
        except Exception as e:
            logger.exception(f"AsyncCache periodic cleanup error: {e}")
            # wait and try again later
            await asyncio.sleep(300)

    def get_stats(self) -> dict[str, Any]:
        now = time.time()
        active_items = sum(1 for item in self._cache.values() if now < item["expires_at"])
        total_access_count = sum(item.get("access_count", 0) for item in self._cache.values())
        return {
            "total_items": len(self._cache),
            "active_items": active_items,
            "expired_items": len(self._cache) - active_items,
            "max_size": self.max_size,
            "utilization_pct": (len(self._cache) / max(1, self.max_size)) * 100,
            "total_access_count": total_access_count,
            "default_ttl": self.ttl,
        }


class MarketDataCache(AsyncCache):
    """
    Specialized cache for market data with domain-specific methods.
    Provides:
      - get_timeseries(uk, start, end, access_token, interval, fetch_func, ttl=...)
    and domain helper caches (option chains, greeks...) implemented async.
    """

    def __init__(self, ttl: int = 30, max_size: int = 2000, max_cache_size: int | None = None):
        # support `max_cache_size` alias for backwards compatibility
        if max_cache_size is not None:
            max_size = max_cache_size
        super().__init__(ttl=ttl, max_size=max_size)

    def _timeseries_key(self, uk: str, start: str, end: str, interval: int) -> str:
        return f"timeseries::{uk}::{start}::{end}::{interval}"

    async def get_timeseries(
        self,
        uk: str,
        start: str,
        end: str,
        access_token: str,
        interval: int,
        fetch_func: Callable[..., Any],
        ttl: int | None = None,
        unit: str | None = "minutes",
    ) -> Any:
        """
        Attempts to return cached timeseries data (usually a pd.DataFrame).
        If cache-miss -> calls fetch_func(access_token, uk, start, end, interval, unit=unit)
        Supports fetch_func as either coroutine function or normal function (runs sync fetchers in a threadpool).
        """
        key = self._timeseries_key(uk, start, end, interval)

        # try cache
        cached = await self.get_by_key(key)
        if cached is not None:
            return cached

        # Not found -> fetch
        try:
            # support coroutine and sync functions
            if asyncio.iscoroutinefunction(fetch_func):
                df = await fetch_func(access_token, uk, start, end, interval, unit=unit)
            else:
                loop = asyncio.get_running_loop()
                # run sync fetcher in default ThreadPoolExecutor
                partial = lambda: fetch_func(access_token, uk, start, end, interval, unit=unit)
                df = await loop.run_in_executor(None, partial)

            # store in cache
            await self.set_by_key(key, df, ttl=ttl)
            return df

        except Exception as e:
            logger.exception(f"Failed to fetch timeseries for key={key}: {e}")
            raise

    # Domain helper methods (async)
    async def cache_option_chain(
        self, symbol: str, expiry: str, data: Any, ttl: int | None = 30
    ) -> bool:
        key = f"option_chain::{symbol}::{expiry}"
        return await self.set_by_key(key, data, ttl=ttl)

    async def get_option_chain(self, symbol: str, expiry: str) -> Any | None:
        key = f"option_chain::{symbol}::{expiry}"
        return await self.get_by_key(key)

    async def cache_greeks(
        self,
        symbol: str,
        strike: str,
        expiry: str,
        option_type: str,
        greeks: Any,
        ttl: int | None = 15,
    ) -> bool:
        key = f"greeks::{symbol}::{strike}::{expiry}::{option_type}"
        return await self.set_by_key(key, greeks, ttl=ttl)

    async def get_greeks(
        self, symbol: str, strike: str, expiry: str, option_type: str
    ) -> Any | None:
        key = f"greeks::{symbol}::{strike}::{expiry}::{option_type}"
        return await self.get_by_key(key)

    async def cache_volatility_surface(
        self, symbol: str, surface_data: Any, ttl: int | None = 300
    ) -> bool:
        key = f"vol_surface::{symbol}"
        return await self.set_by_key(key, surface_data, ttl=ttl)

    async def get_volatility_surface(self, symbol: str) -> Any | None:
        key = f"vol_surface::{symbol}"
        return await self.get_by_key(key)

    async def invalidate_symbol_data(self, symbol: str) -> int:
        # pattern matching for keys containing the symbol
        return await self.delete_pattern(f"*{symbol}*")


# Global default instances (convenience)
market_data_cache = MarketDataCache()
general_cache = AsyncCache(ttl=300, max_size=500)
