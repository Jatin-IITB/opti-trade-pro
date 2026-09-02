# src/options_trading/api/routes/auth.py
"""FastAPI routes for authentication endpoints. Fixed OAuth2 flow with proper user ID handling and error fixes."""

import json
import logging
import secrets
from datetime import datetime, timedelta
from html import escape as html_escape
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from ...config.settings import get_settings
from ...models.auth import (
    AuthStatus,
    OAuthCallbackRequest,
    OAuthCallbackResponse,
    TokenValidationResponse,
)
from ...services.auth_service import AuthService
from ...services.connector_store import ConnectorStore
from ...utils.auth_dependencies import get_current_user
from ...utils.exceptions import AuthError, TokenRefreshError
from ...utils.rate_limit import rate_limit_callback, rate_limit_login, rate_limit_refresh

_connector_store = ConnectorStore()

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["authentication"])

_OAUTH_STATE_TTL = 300
_DEFAULT_RETURN_URL = "/dashboard"


def safe_return_url(raw: str | None) -> str:
    """Reduce a caller-supplied ``return_url`` to a same-origin relative path.

    ``return_url`` is an unauthenticated query parameter that ends up in the
    callback's HTML. Allow-list rather than escape: only a single-slash
    absolute path is accepted.

    Rejected forms and why:
      - ``//evil.com/x``      protocol-relative; a browser treats it as a host
      - ``https://evil.com``  absolute, off-origin
      - ``javascript:...``    scheme injection
      - ``';fetch(...)//``    breaks out of the JS string literal it is
                              interpolated into, executing attacker code on the
                              app origin immediately after login establishes a
                              session against a live broker connection
      - anything with a control character, CR/LF, quote, backslash or ``<``
    """
    if not raw:
        return _DEFAULT_RETURN_URL
    if not raw.startswith("/") or raw.startswith("//"):
        return _DEFAULT_RETURN_URL
    if any(ch in raw for ch in "'\"\\<>\r\n\t"):
        return _DEFAULT_RETURN_URL
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw):
        return _DEFAULT_RETURN_URL
    return raw


def _ensure_pending_store(app):
    if not hasattr(app.state, "pending_oauth_states"):
        app.state.pending_oauth_states = {}


def _prune_states(app):
    now = datetime.now()
    store = getattr(app.state, "pending_oauth_states", {})
    expired = [k for k, v in store.items() if v["expires_at"] <= now]
    for k in expired:
        store.pop(k, None)


@router.get("/login")
@rate_limit_login
async def initiate_login(
    request: Request,
    user_id: str | None = "default",
    return_url: str | None = None,
) -> RedirectResponse:
    """Initiate OAuth2 login flow."""
    settings = get_settings()
    _ensure_pending_store(request.app)
    _prune_states(request.app)

    state_token = secrets.token_urlsafe(32)
    state = f"{user_id}:{state_token}"
    expires_at = datetime.now() + timedelta(seconds=_OAUTH_STATE_TTL)
    request.app.state.pending_oauth_states[state_token] = {
        "user_id": user_id,
        "expires_at": expires_at,
        # Sanitised on the way in as well as on the way out: storing it
        # server-side does not make it trusted.
        "return_url": safe_return_url(return_url),
    }
    logger.debug(
        "Stored server-side oauth state for user=%s token=%s (expires %s)",
        user_id,
        state_token,
        expires_at.isoformat(),
    )

    try:
        request.session["oauth_state"] = state
        request.session["user_id"] = user_id

        connector_config = await _connector_store.get_config("upstox")
        client_id = connector_config["api_key"] if connector_config else settings.upstox_api_key
        redirect_uri = (
            connector_config.get("redirect_uri", settings.oauth_redirect_uri)
            if connector_config
            else settings.oauth_redirect_uri
        )

        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        auth_url = f"https://api-v2.upstox.com/login/authorization/dialog?{urlencode(params)}"
        logger.info(f"Redirecting to OAuth2 authorization: {auth_url}")

        response = RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)
        response.set_cookie(
            "oauth_state", state, max_age=_OAUTH_STATE_TTL, httponly=True, samesite="lax", path="/"
        )
        return response
    except Exception as e:
        logger.error(f"Failed to initiate login: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to initiate authentication",
        )


@router.get("/callback")
@rate_limit_callback
async def oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> HTMLResponse:
    """Handle OAuth2 callback from Upstox with proper user ID handling."""
    if error:
        logger.error(f"OAuth2 error: {error} - {error_description}")
        return HTMLResponse(
            content=f"<b>Error:</b> {error}<br/><b>Description:</b> {error_description or 'Unknown error'}<br/>Please try again.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not code or not state:
        return HTMLResponse(
            content="No authorization code or state received. Please try again.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    try:
        user_id_part, state_token = state.split(":", 1)
    except ValueError:
        logger.error(f"Invalid state format: {state}")
        return HTMLResponse(
            "Invalid state parameter. Possible CSRF attack.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    _ensure_pending_store(request.app)
    _prune_states(request.app)
    stored = request.app.state.pending_oauth_states.pop(state_token, None)
    return_url = stored.get("return_url") if stored else None
    valid_state = False
    if stored and stored.get("user_id") == user_id_part:
        logger.debug("State token validated via server-side store for user=%s", user_id_part)
        valid_state = True
    else:
        try:
            stored_state = request.session.get("oauth_state")
        except Exception:
            stored_state = None
        cookie_state = request.cookies.get("oauth_state")
        if stored_state == state or cookie_state == state:
            logger.debug("State token validated via session/cookie fallback")
            valid_state = True

    if not valid_state:
        logger.warning("State mismatch")
        return HTMLResponse(
            "Invalid state parameter. Possible CSRF attack.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        async with AuthService() as auth_service:
            await auth_service.load_connector_credentials()
            callback_request = OAuthCallbackRequest(code=code, state=state)
            token_info = await auth_service.exchange_code_for_tokens(callback_request)

            actual_user_id = token_info.user_id
            try:
                request.session["authenticated_user_id"] = actual_user_id
                request.session["authenticated"] = True
            except Exception:
                logger.debug("Could not write authenticated flags to session (continuing)")

            await auth_service.storage.store_token(token_info)

            # Initialize services after auth (best-effort)
            try:
                from ...utils.app_init import initialize_app_services

                await initialize_app_services(request.app, access_token=token_info.access_token)
                logger.info(
                    "Application services initialized after successful auth for user %s",
                    actual_user_id,
                )
            except Exception as e:
                logger.warning("Service init after auth failed: %s", e)

            # Re-sanitise at render time, and escape both interpolations:
            # json.dumps for the JS string literal, html.escape for the href.
            redirect_to = safe_return_url(return_url)
            js_target = json.dumps(redirect_to)
            href_target = html_escape(redirect_to, quote=True)
            safe_user = html_escape(str(actual_user_id), quote=True)
            body = f"""
            <p>User ID: <b>{safe_user}</b></p>
            <p>Redirecting you to the dashboard...</p>
            <script>setTimeout(function () {{ window.location.href = {js_target}; }}, 800);</script>
            <p>If nothing happens, <a href="{href_target}">click here</a>.</p>
            """
            response = HTMLResponse(content=body, status_code=status.HTTP_200_OK)
            response.delete_cookie("oauth_state", path="/")
            return response
    except AuthError as e:
        logger.error(f"Auth error during callback: {e}")
        return HTMLResponse(
            content=f"Failed to exchange authorization code for tokens. Error: {e!s}. Please try again.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as e:
        logger.error(f"Unexpected error in OAuth callback: {e}", exc_info=True)
        return HTMLResponse(
            content="An unexpected error occurred during authentication. Please try again or contact support.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.post("/callback/api", response_model=OAuthCallbackResponse)
async def api_oauth_callback(callback: OAuthCallbackRequest) -> OAuthCallbackResponse:
    try:
        async with AuthService() as auth_service:
            await auth_service.load_connector_credentials()
            token_info = await auth_service.exchange_code_for_tokens(callback)
            return OAuthCallbackResponse(
                success=True,
                message="Authentication successful",
                access_token=token_info.access_token,
            )
    except AuthError as e:
        logger.error(f"API OAuth2 callback failed: {e}")
        return OAuthCallbackResponse(success=False, message=f"Authentication failed: {e!s}")


@router.get("/status", response_model=AuthStatus)
async def get_auth_status(request: Request) -> AuthStatus:
    try:
        authenticated_user_id = request.session.get("authenticated_user_id")
        is_authenticated = request.session.get("authenticated", False)
        if authenticated_user_id and is_authenticated:
            user_id = authenticated_user_id
        else:
            async with AuthService() as auth_service:
                stored_users = await auth_service.storage.list_stored_users()
                user_id = stored_users[0] if stored_users else "default"
        async with AuthService() as auth_service:
            return await auth_service.get_auth_status(user_id)
    except Exception as e:
        logger.error(f"Failed to get auth status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve authentication status",
        )


@router.post("/validate", response_model=TokenValidationResponse)
async def validate_token(access_token: str) -> TokenValidationResponse:
    try:
        async with AuthService() as auth_service:
            is_valid = await auth_service.validate_token(access_token)
            if is_valid:
                try:
                    profile = await auth_service.get_user_profile(access_token)
                    return TokenValidationResponse(valid=True, user_id=profile.user_id)
                except AuthError:
                    return TokenValidationResponse(valid=True)
            else:
                return TokenValidationResponse(valid=False, error="Token is invalid or expired")
    except Exception as e:
        logger.error(f"Token validation failed: {e}")
        return TokenValidationResponse(valid=False, error=f"Validation error: {e!s}")


@router.post("/refresh")
@rate_limit_refresh
async def refresh_token(
    request: Request, refresh_token: str, user_id: str = "default"
) -> dict[str, str]:
    try:
        async with AuthService() as auth_service:
            token_info = await auth_service.refresh_token(refresh_token, user_id)
            return {
                "access_token": token_info.access_token,
                "expires_at": token_info.expires_at.isoformat(),
                "user_id": token_info.user_id,
                "message": "Token refreshed successfully",
            }
    except TokenRefreshError as e:
        logger.error(f"Token refresh failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Token refresh failed: {e!s}"
        )


@router.post("/logout")
async def logout(request: Request) -> dict[str, str]:
    try:
        authenticated_user_id = request.session.get("authenticated_user_id", "default")
        async with AuthService() as auth_service:
            await auth_service.logout(authenticated_user_id)
        request.session.clear()
        return {"message": "Logged out successfully"}
    except Exception as e:
        logger.error(f"Logout failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Logout failed"
        )


@router.get("/profile")
async def get_profile(user: dict = Depends(get_current_user)) -> dict:
    return user
