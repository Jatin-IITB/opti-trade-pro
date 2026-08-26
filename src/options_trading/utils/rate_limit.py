# src/options_trading/utils/rate_limit.py
"""In-memory sliding-window rate limiter for FastAPI endpoints."""

import time
from collections import defaultdict
from collections.abc import Callable
from functools import wraps
from threading import Lock

from fastapi import HTTPException, Request, status


class _SlidingWindow:
    """Thread-safe sliding-window counter per key."""

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            timestamps = self._hits[key]
            self._hits[key] = [t for t in timestamps if t > cutoff]
            if len(self._hits[key]) >= self.max_requests:
                return False
            self._hits[key].append(now)
            return True


_LOGIN_LIMITER = _SlidingWindow(max_requests=10, window_seconds=60)
_CALLBACK_LIMITER = _SlidingWindow(max_requests=10, window_seconds=60)
_REFRESH_LIMITER = _SlidingWindow(max_requests=5, window_seconds=60)


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit_login(func: Callable) -> Callable:
    @wraps(func)
    async def wrapper(*args, **kwargs):
        request: Request = kwargs.get("request") or args[0]
        if not _LOGIN_LIMITER.is_allowed(_client_key(request)):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts — try again later",
            )
        return await func(*args, **kwargs)

    return wrapper


def rate_limit_callback(func: Callable) -> Callable:
    @wraps(func)
    async def wrapper(*args, **kwargs):
        request: Request = kwargs.get("request") or args[0]
        if not _CALLBACK_LIMITER.is_allowed(_client_key(request)):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many callback attempts — try again later",
            )
        return await func(*args, **kwargs)

    return wrapper


def rate_limit_refresh(func: Callable) -> Callable:
    @wraps(func)
    async def wrapper(*args, **kwargs):
        request: Request = kwargs.get("request") or args[0]
        if not _REFRESH_LIMITER.is_allowed(_client_key(request)):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many refresh attempts — try again later",
            )
        return await func(*args, **kwargs)

    return wrapper
