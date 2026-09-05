"""Tests for the Upstox token provider.

The behaviour under test is what keeps an unattended sync alive across the
daily Upstox token expiry: resolve lazily, cache until shortly before expiry,
re-resolve after that, and report an auth failure as a distinct, actionable
state rather than as one more transient error.

The clock is injected throughout — no wall-clock or network dependence
(CLAUDE.md: deterministic tests).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from options_trading.models.auth import TokenInfo
from options_trading.services.token_provider import (
    TokenProvider,
    TokenProviderConfig,
    get_token_provider,
)
from options_trading.utils.exceptions import AuthError


class FakeClock:
    """Monotonic clock the test advances explicitly."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _token_info(expires_in_seconds: float, access_token: str = "tok") -> TokenInfo:
    return TokenInfo(
        access_token=access_token,
        expires_at=datetime.now() + timedelta(seconds=expires_in_seconds),
        user_id="default",
    )


def _auth_factory(
    tokens: list[str] | None = None,
    token_info: TokenInfo | None = None,
    error: Exception | None = None,
) -> tuple[MagicMock, dict]:
    """Build an AuthService factory plus a dict recording call counts."""
    calls = {"resolve": 0}
    seq = iter(tokens or ["tok"])

    async def get_valid_access_token(user_id="default"):
        calls["resolve"] += 1
        if error is not None:
            raise error
        return next(seq)

    auth = MagicMock()
    auth.get_valid_access_token = AsyncMock(side_effect=get_valid_access_token)
    auth.storage.load_token = AsyncMock(return_value=token_info)
    auth.__aenter__ = AsyncMock(return_value=auth)
    auth.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=auth), calls


class TestResolution:
    @pytest.mark.asyncio
    async def test_resolves_a_token(self):
        factory, calls = _auth_factory(["abc"], _token_info(86_400))
        provider = TokenProvider(auth_service_factory=factory, now_fn=FakeClock())
        assert await provider.get() == "abc"
        assert calls["resolve"] == 1

    @pytest.mark.asyncio
    async def test_second_call_uses_cache(self):
        factory, calls = _auth_factory(["abc", "xyz"], _token_info(86_400))
        provider = TokenProvider(auth_service_factory=factory, now_fn=FakeClock())
        assert await provider.get() == "abc"
        assert await provider.get() == "abc"
        assert calls["resolve"] == 1, "cached token must not re-hit AuthService"

    @pytest.mark.asyncio
    async def test_reresolves_after_cache_expires(self):
        clock = FakeClock()
        factory, calls = _auth_factory(["first", "second"], _token_info(86_400))
        provider = TokenProvider(
            auth_service_factory=factory,
            config=TokenProviderConfig(max_cache_seconds=100.0),
            now_fn=clock,
        )
        assert await provider.get() == "first"
        clock.advance(101.0)
        assert await provider.get() == "second"
        assert calls["resolve"] == 2

    @pytest.mark.asyncio
    async def test_cache_never_outlives_the_token(self):
        """A token expiring sooner than max_cache_seconds shortens the cache."""
        clock = FakeClock()
        # Expires in 400s with a 300s buffer -> trust for only ~100s, even
        # though max_cache_seconds is 900.
        factory, calls = _auth_factory(["first", "second"], _token_info(400))
        provider = TokenProvider(
            auth_service_factory=factory,
            config=TokenProviderConfig(refresh_buffer_seconds=300.0, max_cache_seconds=900.0),
            now_fn=clock,
        )
        assert await provider.get() == "first"
        clock.advance(150.0)
        assert await provider.get() == "second"
        assert calls["resolve"] == 2

    @pytest.mark.asyncio
    async def test_invalidate_forces_reresolution(self):
        factory, calls = _auth_factory(["first", "second"], _token_info(86_400))
        provider = TokenProvider(auth_service_factory=factory, now_fn=FakeClock())
        assert await provider.get() == "first"
        provider.invalidate()
        assert await provider.get() == "second"
        assert calls["resolve"] == 2

    @pytest.mark.asyncio
    async def test_concurrent_callers_resolve_once(self):
        """Simultaneous callers must not stampede AuthService."""
        factory, calls = _auth_factory(["a", "b", "c", "d", "e"], _token_info(86_400))
        provider = TokenProvider(auth_service_factory=factory, now_fn=FakeClock())
        results = await asyncio.gather(*(provider.get() for _ in range(5)))
        assert results == ["a"] * 5
        assert calls["resolve"] == 1

    @pytest.mark.asyncio
    async def test_missing_token_metadata_falls_back_to_max_cache(self):
        factory, _ = _auth_factory(["abc"], token_info=None)
        provider = TokenProvider(auth_service_factory=factory, now_fn=FakeClock())
        assert await provider.get() == "abc"


class TestAuthFailure:
    @pytest.mark.asyncio
    async def test_auth_error_propagates(self):
        factory, _ = _auth_factory(error=AuthError("no tokens"))
        provider = TokenProvider(auth_service_factory=factory, now_fn=FakeClock())
        with pytest.raises(AuthError):
            await provider.get()

    @pytest.mark.asyncio
    async def test_sets_needs_reauth(self):
        factory, _ = _auth_factory(error=AuthError("no tokens"))
        provider = TokenProvider(auth_service_factory=factory, now_fn=FakeClock())
        with pytest.raises(AuthError):
            await provider.get()
        assert provider.needs_reauth is True
        assert "no tokens" in (provider.last_auth_error or "")

    @pytest.mark.asyncio
    async def test_backoff_avoids_hammering_auth_service(self):
        """Re-auth needs a human; retrying in a tight loop cannot help."""
        clock = FakeClock()
        factory, calls = _auth_factory(error=AuthError("no tokens"))
        provider = TokenProvider(
            auth_service_factory=factory,
            config=TokenProviderConfig(auth_failure_backoff_seconds=60.0),
            now_fn=clock,
        )
        for _ in range(4):
            with pytest.raises(AuthError):
                await provider.get()
        assert calls["resolve"] == 1, "backoff must suppress repeat resolution"

        clock.advance(61.0)
        with pytest.raises(AuthError):
            await provider.get()
        assert calls["resolve"] == 2, "backoff must lapse"

    @pytest.mark.asyncio
    async def test_recovery_clears_the_flag(self):
        clock = FakeClock()
        auth = MagicMock()
        state = {"fail": True}

        async def resolve(user_id="default"):
            if state["fail"]:
                raise AuthError("no tokens")
            return "recovered"

        auth.get_valid_access_token = AsyncMock(side_effect=resolve)
        auth.storage.load_token = AsyncMock(return_value=_token_info(86_400))
        auth.__aenter__ = AsyncMock(return_value=auth)
        auth.__aexit__ = AsyncMock(return_value=False)

        provider = TokenProvider(
            auth_service_factory=MagicMock(return_value=auth),
            config=TokenProviderConfig(auth_failure_backoff_seconds=60.0),
            now_fn=clock,
        )
        with pytest.raises(AuthError):
            await provider.get()
        assert provider.needs_reauth is True

        state["fail"] = False
        clock.advance(61.0)
        assert await provider.get() == "recovered"
        assert provider.needs_reauth is False
        assert provider.last_auth_error is None

    @pytest.mark.asyncio
    async def test_unexpected_error_becomes_auth_error_not_stale_token(self):
        factory, _ = _auth_factory(error=RuntimeError("network down"))
        provider = TokenProvider(auth_service_factory=factory, now_fn=FakeClock())
        with pytest.raises(AuthError, match="network down"):
            await provider.get()


class TestCached:
    def test_raises_before_first_resolution(self):
        """A never-resolved provider must not yield an empty bearer header."""
        provider = TokenProvider(now_fn=FakeClock())
        with pytest.raises(AuthError):
            provider.cached()

    @pytest.mark.asyncio
    async def test_returns_last_resolved_without_io(self):
        factory, calls = _auth_factory(["abc"], _token_info(86_400))
        provider = TokenProvider(auth_service_factory=factory, now_fn=FakeClock())
        await provider.get()
        assert provider.cached() == "abc"
        assert calls["resolve"] == 1


class TestGetTokenProvider:
    def test_creates_and_reuses_one_instance_per_user(self):
        app = MagicMock()
        app.state = MagicMock(spec=[])  # no provider registry yet
        first = get_token_provider(app)
        assert isinstance(first, TokenProvider)
        assert get_token_provider(app) is first

    def test_different_users_get_different_providers(self):
        """A shared instance would resolve the creator's token for everyone.

        Regression guard: the registry was a single slot, so the ``user_id``
        argument was honoured only on the very first call and every later
        caller silently got another account's token.
        """
        app = MagicMock()
        app.state = MagicMock(spec=[])
        default = get_token_provider(app, "default")
        other = get_token_provider(app, "AB1234")

        assert other is not default
        assert other._user_id == "AB1234"
        assert default._user_id == "default"
        assert get_token_provider(app, "AB1234") is other


class TestInvalidateFailsClosed:
    @pytest.mark.asyncio
    async def test_invalidate_clears_the_token(self):
        """A 401 means the broker declared the token dead.

        ``cached()`` feeds the synchronous capture path, so leaving the token
        in place would keep sending a known-dead bearer header.
        """
        factory, _ = _auth_factory(["abc"], _token_info(86_400))
        provider = TokenProvider(auth_service_factory=factory, now_fn=FakeClock())
        await provider.get()
        assert provider.cached() == "abc"

        provider.invalidate()
        with pytest.raises(AuthError):
            provider.cached()


class TestAnalyticsToken:
    """The Analytics Token: read-only, GET-only, valid one year.

    The daily access token expires at 03:30 IST, so unattended capture needed
    an interactive login every morning and one forgotten morning cost a whole
    trading day of history. The Analytics Token removes that, and its
    read-only restriction happens to match this app's own rule that no
    order-placement path exists — so it is strictly less privilege, not more.
    """

    @pytest.fixture()
    def configured(self, monkeypatch):
        from pydantic import SecretStr

        from options_trading.services import token_provider as module

        monkeypatch.setattr(
            module.settings, "upstox_analytics_token", SecretStr("analytics-abc"), raising=False
        )
        return "analytics-abc"

    @pytest.mark.asyncio
    async def test_it_is_used_without_touching_auth_service(self, configured):
        """No OAuth round trip, so no login can be needed."""
        factory, calls = _auth_factory(["oauth-token"], _token_info(86_400))
        provider = TokenProvider(auth_service_factory=factory, now_fn=FakeClock())

        assert await provider.get() == configured
        assert calls["resolve"] == 0, "the OAuth path must not be consulted"

    @pytest.mark.asyncio
    async def test_no_reauth_is_ever_requested(self, configured):
        """Prompting for a login that cannot help is worse than silence."""
        factory, _ = _auth_factory(error=AuthError("expired"))
        provider = TokenProvider(auth_service_factory=factory, now_fn=FakeClock())
        await provider.get()

        assert provider.needs_reauth is False

    @pytest.mark.asyncio
    async def test_rejection_falls_back_instead_of_looping(self, configured):
        """Re-resolving a constant returns the same constant.

        Without the rejected flag, invalidate/get would hand back the same
        dead token on every retry forever — a fail-closed check that loops
        instead of failing.
        """
        factory, calls = _auth_factory(["oauth-token"], _token_info(86_400))
        provider = TokenProvider(auth_service_factory=factory, now_fn=FakeClock())
        assert await provider.get() == configured

        provider.invalidate()

        assert await provider.get() == "oauth-token"
        assert calls["resolve"] == 1
        assert provider.needs_reauth is False  # a fresh OAuth token resolved fine

    @pytest.mark.asyncio
    async def test_after_rejection_reauth_reporting_returns(self, configured):
        """Once the static token is gone, OAuth really is what is left."""
        factory, _ = _auth_factory(error=AuthError("login required"))
        clock = FakeClock()
        provider = TokenProvider(auth_service_factory=factory, now_fn=clock)
        await provider.get()
        provider.invalidate()

        with pytest.raises(AuthError):
            await provider.get()
        assert provider.needs_reauth is True

    @pytest.mark.asyncio
    async def test_absent_token_leaves_the_oauth_path_untouched(self, monkeypatch):
        from options_trading.services import token_provider as module

        monkeypatch.setattr(module.settings, "upstox_analytics_token", None, raising=False)
        factory, calls = _auth_factory(["oauth-token"], _token_info(86_400))
        provider = TokenProvider(auth_service_factory=factory, now_fn=FakeClock())

        assert await provider.get() == "oauth-token"
        assert calls["resolve"] == 1

    @pytest.mark.asyncio
    async def test_a_blank_token_counts_as_absent(self, monkeypatch):
        """An empty env var must not become an empty bearer header."""
        from pydantic import SecretStr

        from options_trading.services import token_provider as module

        monkeypatch.setattr(
            module.settings, "upstox_analytics_token", SecretStr("   "), raising=False
        )
        factory, calls = _auth_factory(["oauth-token"], _token_info(86_400))
        provider = TokenProvider(auth_service_factory=factory, now_fn=FakeClock())

        assert await provider.get() == "oauth-token"
        assert calls["resolve"] == 1

    @pytest.mark.asyncio
    async def test_the_secret_never_reaches_the_logs(self, configured, caplog):
        """A year-long credential must not survive in a log file.

        CLAUDE.md: never echo secret values, presence and length only.
        """
        factory, _ = _auth_factory(["oauth-token"], _token_info(86_400))
        provider = TokenProvider(auth_service_factory=factory, now_fn=FakeClock())

        with caplog.at_level("DEBUG"):
            await provider.get()
            provider.invalidate()

        assert configured not in caplog.text


def test_the_suite_never_sees_a_real_analytics_token():
    """conftest neutralises it, and this is what keeps that true.

    TokenProvider reads the token from settings, so a developer with one in
    .env takes a different branch here than CI does: fourteen tests in this
    file failed exactly that way once. A suite whose result depends on whose
    machine ran it is as broken as one that depends on the wall clock.

    It is also a disclosure control. A failing assertion prints both sides of
    the comparison, so a real token would be written to pytest output and CI
    logs in plaintext — which is how the original failure surfaced it.
    """
    from options_trading.config.settings import settings

    configured = settings.upstox_analytics_token
    value = configured.get_secret_value().strip() if configured else ""

    assert value == "", "the suite must run against no Analytics Token, whatever .env holds"
    assert TokenProvider._analytics_token() is None
