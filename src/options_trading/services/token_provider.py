"""Supplies a currently-valid Upstox access token to long-running services.

Upstox access tokens expire daily (around 03:30 IST). Standard app types are
not issued a refresh token, so expiry means the user must re-authenticate
through the OAuth flow — it cannot be recovered from automatically.

The failure mode this module exists to prevent: background services used to
capture the token *string* at construction and hold it for their lifetime, so
the moment it expired they logged an identical failure every cycle, forever,
with nothing surfaced to the user. Services now resolve the token through a
provider on each use.

Resolution is cached because ``AuthService.get_valid_access_token`` performs a
network round-trip to validate; calling it per broker request would triple the
outbound call volume.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..models.auth import TokenInfo
from ..utils.exceptions import AuthError
from .auth_service import AuthService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TokenProviderConfig:
    """Cache tuning for :class:`TokenProvider`."""

    # Re-resolve this long before the token's own expiry, so an in-flight
    # request cannot straddle the boundary.
    refresh_buffer_seconds: float = 300.0
    # Upper bound on cache lifetime even for a long-dated token, so a token
    # revoked broker-side is noticed within this window rather than at expiry.
    max_cache_seconds: float = 900.0
    # After an auth failure, wait this long before hitting AuthService again.
    # Re-authentication needs human action; retrying hard cannot help.
    auth_failure_backoff_seconds: float = 60.0


class TokenProvider:
    """Resolves and caches a valid Upstox access token.

    Safe for concurrent use: simultaneous callers share one resolution rather
    than stampeding ``AuthService``.
    """

    def __init__(
        self,
        user_id: str = "default",
        auth_service_factory: Callable[[], AuthService] = AuthService,
        config: TokenProviderConfig = TokenProviderConfig(),
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._user_id = user_id
        self._auth_service_factory = auth_service_factory
        self._config = config
        self._now_fn = now_fn

        self._token: str | None = None
        self._good_until: float = 0.0
        self._auth_failed_until: float = 0.0
        self._last_auth_error: str | None = None
        self._lock = asyncio.Lock()

    @property
    def needs_reauth(self) -> bool:
        """True if the last resolution failed and the backoff has not elapsed.

        Callers surface this to the user: only an interactive Upstox login can
        clear it.
        """
        return self._last_auth_error is not None and self._now_fn() < self._auth_failed_until

    @property
    def last_auth_error(self) -> str | None:
        return self._last_auth_error

    def cached(self) -> str:
        """Last resolved token, without any I/O.

        For synchronous call paths that run inside a worker thread and cannot
        await. Raises if no token has ever been resolved, so a caller can never
        silently send an empty bearer header.
        """
        if self._token is None:
            raise AuthError("No Upstox token has been resolved yet")
        return self._token

    def invalidate(self) -> None:
        """Drop the cached token so the next :meth:`get` re-resolves.

        Called when the broker rejects a request with 401 — i.e. it has told
        us this token is dead. The token itself is cleared, not just its
        deadline, so :meth:`cached` raises instead of handing a known-dead
        token to the synchronous capture path (ADR-008, fail closed).
        """
        self._token = None
        self._good_until = 0.0

    async def get(self) -> str:
        """Return a valid access token, resolving or refreshing as needed.

        Raises ``AuthError`` if no valid token can be obtained — the caller
        must treat that as "user must log in again", not as a transient error.
        """
        now = self._now_fn()
        if self._token is not None and now < self._good_until:
            return self._token

        async with self._lock:
            # Another caller may have resolved while we waited for the lock.
            now = self._now_fn()
            if self._token is not None and now < self._good_until:
                return self._token

            if self._last_auth_error is not None and now < self._auth_failed_until:
                raise AuthError(f"Upstox re-authentication required: {self._last_auth_error}")

            return await self._resolve()

    async def _resolve(self) -> str:
        try:
            async with self._auth_service_factory() as auth:
                token = await auth.get_valid_access_token(self._user_id)
                token_info = await self._load_token_info(auth)
        except AuthError as exc:
            self._note_auth_failure(str(exc))
            raise
        except Exception as exc:
            # An unexpected failure is not proof that re-auth is needed, so it
            # does not set the backoff — but it must not yield a stale token.
            logger.warning("Token resolution failed unexpectedly: %s", exc)
            raise AuthError(f"Could not resolve Upstox token: {exc}") from exc

        self._token = token
        self._last_auth_error = None
        self._auth_failed_until = 0.0
        self._good_until = self._now_fn() + self._cache_seconds_for(token_info)
        logger.info(
            "Upstox token resolved; cached for %.0fs",
            self._good_until - self._now_fn(),
        )
        return token

    async def _load_token_info(self, auth: AuthService) -> TokenInfo | None:
        """Best-effort read of token metadata, for expiry-aware caching."""
        try:
            return await auth.storage.load_token(self._user_id)
        except Exception:
            logger.debug("Could not load token metadata; using default cache TTL")
            return None

    def _cache_seconds_for(self, token_info: TokenInfo | None) -> float:
        """Seconds to trust the resolved token, floored at zero."""
        ttl = self._config.max_cache_seconds
        if token_info is not None:
            until_expiry = token_info.time_until_expiry.total_seconds()
            ttl = min(ttl, until_expiry - self._config.refresh_buffer_seconds)
        return max(ttl, 0.0)

    def _note_auth_failure(self, message: str) -> None:
        self._last_auth_error = message
        self._auth_failed_until = self._now_fn() + self._config.auth_failure_backoff_seconds
        self._token = None
        self._good_until = 0.0
        logger.warning("Upstox authentication required: %s", message)


def get_token_provider(app, user_id: str = "default") -> TokenProvider:
    """Return the application's provider for ``user_id``, creating it on first use.

    Keyed by user: a single shared instance would honour only the ``user_id``
    of whichever caller created it, and every later caller would silently
    resolve *that* user's token. One provider per user still means a refresh
    is shared by every service acting for that user.

    ``app.state.token_provider`` is kept pointing at the default user's
    provider for callers that read it directly.
    """
    providers = getattr(app.state, "token_providers", None)
    if providers is None:
        providers = {}
        app.state.token_providers = providers

    provider = providers.get(user_id)
    if provider is None:
        provider = TokenProvider(user_id=user_id)
        providers[user_id] = provider
        logger.debug("Created TokenProvider for user %s", user_id)

    if getattr(app.state, "token_provider", None) is None:
        app.state.token_provider = provider
    return provider


__all__ = ["TokenProvider", "TokenProviderConfig", "get_token_provider"]
