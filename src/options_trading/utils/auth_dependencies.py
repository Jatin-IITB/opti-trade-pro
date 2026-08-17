# src/options_trading/utils/auth_dependencies.py
"""
Robust authentication dependencies for FastAPI endpoints (API + browser dashboard).

Compatible with:
- src/options_trading/services/auth_service.py (AuthService)
- src/options_trading/utils/security.py (SecureStorage)
- src/options_trading/models/auth.py (UserProfile, TokenInfo)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config.settings import get_settings
from ..models.auth import TokenInfo, UserProfile
from ..services.auth_service import AuthService
from ..utils.exceptions import AuthError
from ..utils.security import SecureStorage

logger = logging.getLogger(__name__)
_settings = get_settings()

# sensible defaults if your settings don't expose these
AUTH_VALIDATE_TIMEOUT = getattr(_settings, "AUTH_VALIDATE_TIMEOUT", 5)  # seconds
AUTH_REFRESH_TIMEOUT = getattr(_settings, "AUTH_REFRESH_TIMEOUT", 8)  # seconds
AUTH_NEAR_EXPIRY_SECONDS = getattr(_settings, "AUTH_NEAR_EXPIRY_SECONDS", 60)
TRUST_STORED_ON_NETWORK_ERROR = getattr(_settings, "TRUST_STORED_TOKEN_ON_NETWORK_ERROR", True)

# HTTP bearer for API endpoints (auto_error=False => we'll handle missing token ourselves)
security = HTTPBearer(auto_error=False)


async def _with_timeout(awaitable: Awaitable, timeout: float | None, name: str = "auth"):
    """Run awaitable with timeout; convert TimeoutError into AuthError for caller to handle."""
    if timeout is None or timeout <= 0:
        return await awaitable
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout)
    except TimeoutError as e:
        logger.warning("%s timed out after %s seconds", name, timeout)
        raise AuthError(f"{name} timeout") from e


async def _extract_bearer_from_headers(
    credentials: HTTPAuthorizationCredentials | None, authorization: str | None
) -> str | None:
    if credentials and credentials.scheme.lower() == "bearer":
        return credentials.credentials
    if authorization and authorization.startswith("Bearer "):
        return authorization.split(" ", 1)[1].strip()
    return None


def _build_user_dict_from_profile(profile: UserProfile, token: str | None = None) -> dict[str, Any]:
    return {
        "user_id": profile.user_id,
        "user_name": getattr(profile, "user_name", None) or getattr(profile, "username", None),
        "email": getattr(profile, "email", None),
        "permissions": getattr(profile, "permissions", []) or [],
        "token": token,
    }


async def _validate_and_fetch_profile(access_token: str) -> dict[str, Any]:
    """Validate token against AuthService and return user dict. Raises HTTPException on failure."""
    try:
        async with AuthService() as auth_service:
            ok = await _with_timeout(
                auth_service.validate_token(access_token), AUTH_VALIDATE_TIMEOUT, "validate_token"
            )
            if not ok:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or expired token",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            profile = await _with_timeout(
                auth_service.get_user_profile(access_token),
                AUTH_VALIDATE_TIMEOUT,
                "get_user_profile",
            )

            return _build_user_dict_from_profile(profile, access_token)

    except AuthError as e:
        logger.warning("AuthService error during validation/fetch: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication service error"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected auth error: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication failed"
        )


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    authorization: str | None = Header(None),
    force_validate: bool = False,
) -> dict[str, Any]:
    """
    Primary dependency: tries bearer header, then session SecureStorage, then fallback stored user.
    Graceful fallback on transient network errors if a cached, non-expired token exists.
    """
    # 1) Header Bearer token (API clients)
    token = await _extract_bearer_from_headers(credentials, authorization)
    if token:
        # For header tokens we validate immediately (APIs expect realtime validation)
        return await _validate_and_fetch_profile(token)

    # 2) Session-based flow (browser)
    session_user_id: str | None = None
    try:
        session_user_id = request.session.get("authenticated_user_id")
    except Exception:
        session_user_id = None  # sessions might not be configured

    storage = SecureStorage()

    if session_user_id:
        try:
            token_info: TokenInfo | None = await storage.load_token(session_user_id)
            if token_info:
                # compute near-expiry
                is_near_expiry = False
                try:
                    if getattr(token_info, "expires_at", None):
                        now = datetime.now(UTC)
                        expires_at = token_info.expires_at
                        if expires_at.tzinfo is None:
                            expires_at = expires_at.replace(tzinfo=UTC)
                        is_near_expiry = (
                            expires_at - now
                        ).total_seconds() <= AUTH_NEAR_EXPIRY_SECONDS
                except Exception:
                    # if anything odd, treat as not near expiry by default
                    is_near_expiry = False

                # token still valid and not near expiry -> return cached token info (no external call)
                if (
                    not getattr(token_info, "is_expired", False)
                    and not is_near_expiry
                    and not force_validate
                ):
                    return {"user_id": token_info.user_id, "token": token_info.access_token}

                # Otherwise attempt refresh or validation
                async with AuthService() as auth_service:
                    try:
                        # If expired and refresh available -> refresh
                        if getattr(token_info, "is_expired", False) and getattr(
                            token_info, "refresh_token", None
                        ):
                            refreshed: TokenInfo = await _with_timeout(
                                auth_service.refresh_token(
                                    token_info.refresh_token, token_info.user_id
                                ),
                                AUTH_REFRESH_TIMEOUT,
                                "refresh_token",
                            )
                            # persist refreshed token (store_token exists in your SecureStorage)
                            try:
                                await storage.store_token(refreshed)
                            except Exception:
                                logger.debug("Could not persist refreshed token (optional)")

                            # attempt to fetch profile for refreshed token
                            try:
                                profile = await _with_timeout(
                                    auth_service.get_user_profile(refreshed.access_token),
                                    AUTH_VALIDATE_TIMEOUT,
                                    "get_user_profile_after_refresh",
                                )
                                return _build_user_dict_from_profile(
                                    profile, refreshed.access_token
                                )
                            except Exception:
                                return {
                                    "user_id": refreshed.user_id,
                                    "token": refreshed.access_token,
                                }

                        # Not expired but forced validate or near expiry -> validate
                        if force_validate or is_near_expiry:
                            ok = await _with_timeout(
                                auth_service.validate_token(token_info.access_token),
                                AUTH_VALIDATE_TIMEOUT,
                                "validate_token_session",
                            )
                            if ok:
                                try:
                                    profile = await _with_timeout(
                                        auth_service.get_user_profile(token_info.access_token),
                                        AUTH_VALIDATE_TIMEOUT,
                                        "get_user_profile_session",
                                    )
                                    return _build_user_dict_from_profile(
                                        profile, token_info.access_token
                                    )
                                except Exception:
                                    return {
                                        "user_id": token_info.user_id,
                                        "token": token_info.access_token,
                                    }
                            # invalid
                            raise HTTPException(
                                status_code=status.HTTP_401_UNAUTHORIZED, detail="Token invalid"
                            )

                        # If we get here and no refresh/validate needed then fallback to stored token
                        return {"user_id": token_info.user_id, "token": token_info.access_token}

                    except AuthError as e:
                        logger.warning(
                            "AuthService reported error for session user %s: %s", session_user_id, e
                        )
                        # Fallback to trusting stored token when network/DNS issues are transient
                        if (
                            token_info
                            and not getattr(token_info, "is_expired", True)
                            and TRUST_STORED_ON_NETWORK_ERROR
                        ):
                            logger.warning(
                                "Falling back to stored token due to AuthService/network failure."
                            )
                            return {"user_id": token_info.user_id, "token": token_info.access_token}
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Authentication required",
                        )

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(
                "Failed to load token from SecureStorage for session user %s: %s",
                session_user_id,
                e,
            )

    # 3) Fallback: try first stored, non-expired user
    try:
        users = await storage.list_stored_users()
        for u in users:
            try:
                token_info = await storage.load_token(u)
                if token_info and not getattr(token_info, "is_expired", True):
                    # set session for convenience
                    try:
                        request.session["authenticated_user_id"] = token_info.user_id
                        request.session["authenticated"] = True
                    except Exception:
                        pass
                    return {"user_id": token_info.user_id, "token": token_info.access_token}
            except Exception:
                logger.debug("Could not load token for stored user %s", u)
    except Exception as e:
        logger.debug("Error while listing stored users: %s", e)

    # Nothing worked -> unauthorized
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")


async def get_optional_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    authorization: str | None = Header(None),
) -> dict[str, Any] | None:
    """Return user dict if authenticated, otherwise None."""
    try:
        return await get_current_user(request, credentials, authorization, force_validate=False)
    except HTTPException:
        return None


def require_permission(permission: str) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Dependency factory: user = Depends(require_permission('admin'))"""

    async def permission_dependency(
        user: dict[str, Any] = Depends(get_current_user),
    ) -> dict[str, Any]:
        perms: list[str] = user.get("permissions", []) or []
        perms = [str(p).lower() for p in perms]
        if permission.lower() not in perms and "admin" not in perms:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=f"Permission '{permission}' required"
            )
        return user

    return permission_dependency
