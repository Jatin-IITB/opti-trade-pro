# api/auth.py
"""
OAuth2 token-handling with auto-refresh, keyring/file fallback, Flask webhook, and prod-safe enhancements.
"""

import json
import logging
import os
import time
import webbrowser
import threading
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, request, abort

from constants import (
    UPSTOX_AUTH_URL, UPSTOX_BASE_URL, UPSTOX_OAUTH_DIALOG_URL, TOKEN_STORAGE_DIR, TOKEN_FILE_NAME,
    TOKEN_KEYRING_SERVICE, TOKEN_KEYRING_USERNAME, TOKEN_EXPIRY_BUFFER_MINUTES,
    OAUTH_TIMEOUT_SECONDS, DEFAULT_REDIRECT_PORT, DEFAULT_API_VERSION,
    DEFAULT_ACCEPT_HEADER, DEFAULT_CONTENT_TYPE, API_TIMEOUT_SECONDS,
    API_RETRY_ATTEMPTS, API_RETRY_DELAY_SECONDS, DEFAULT_WEBHOOK_PORT
)

from api.exceptions import AuthError, OAuthCallbackTimeout, TokenRefreshError, ConfigError

# ========== Environment ==========
load_dotenv(dotenv_path=Path(__file__).parent.parent / "config" / ".env")
logger = logging.getLogger(__name__)

# ========== Keyring Support ==========
try:
    import keyring
    KEYRING_AVAILABLE = True
except ImportError:
    KEYRING_AVAILABLE = False
    logger.warning("Keyring not available, falling back to file storage")

# ========== Flask App for OAuth Callback ==========
app = Flask(__name__)
_code_container = {}

@app.route('/callback')
def oauth_callback():
    code = request.args.get('code')
    if not code:
        logger.error("No authorization code received")
        abort(400, "Authorization code not found")
    _code_container['code'] = code
    return "Authorization successful! You can close this window.", 200

def _run_flask_server(port: int, ssl_context=None):
    """
    Start Flask in background thread; SSL if provided, HTTP otherwise.
    """
    server = threading.Thread(
        target=app.run,
        kwargs={"host": "0.0.0.0", "port": port, "ssl_context": ssl_context, "debug": False},
        daemon=True
    )
    server.start()
    return server

# ========== Token Manager Class ==========
class TokenManager:
    """Secure token manager for Upstox. Supports auto-refresh, keyring, file fallback."""
    def __init__(self, user_id='default', no_browser=False):
        self.api_key = os.getenv("API_KEY")
        self.secret_key = os.getenv("SECRET_KEY")
        self.redirect_url = os.getenv("REDIRECT_URL", f"http://localhost:{DEFAULT_REDIRECT_PORT}/callback")
        self.webhook_endpoint = os.getenv("WEBHOOK_ENDPOINT", f"http://localhost:{DEFAULT_WEBHOOK_PORT}/callback")
        self.no_browser = no_browser # For headless/dev/CI
        
        if not all([self.api_key, self.secret_key]):
            raise ConfigError("Missing required environment variables: API_KEY, SECRET_KEY")

        # Multi-user prod: append user_id to filename/key
        self.token_file = (TOKEN_STORAGE_DIR / f"{user_id}_{TOKEN_FILE_NAME}") if user_id else (TOKEN_STORAGE_DIR / TOKEN_FILE_NAME)
        self._keyring_user = f"{TOKEN_KEYRING_USERNAME}_{user_id}" if user_id else TOKEN_KEYRING_USERNAME
        self._cached_token = None

    def get_valid_access_token(self) -> str:
        """Get a valid access token: load, refresh, or perform OAuth as needed."""
        logger.info("Checking for existing tokens...")

        token_info = self._load_tokens()
        if token_info:
            logger.info(f"Found saved tokens from {token_info['created_at']}")
            if self._is_token_valid(token_info):
                if self._validate_token_with_api(token_info["access_token"]):
                    logger.info("Using cached access token")
                    return token_info["access_token"]
                else:
                    logger.warning("Cached token invalid, attempting refresh...")
                    if token_info.get("refresh_token"):
                        refreshed = self._refresh_access_token(token_info["refresh_token"])
                        if refreshed:
                            return refreshed
                        raise TokenRefreshError("Refresh token present but refresh failed")
            else:
                logger.info("Token expired, attempting refresh...")
                if token_info.get("refresh_token"):
                    refreshed = self._refresh_access_token(token_info["refresh_token"])
                    if refreshed:
                        return refreshed
                    raise TokenRefreshError("Refresh token present but refresh failed")

        logger.info("No valid tokens found, starting fresh authentication...")
        return self._authenticate_fresh()

    def _load_tokens(self) -> dict:
        """Load tokens from keyring or file fallback."""
        if self._cached_token:
            return self._cached_token
        if KEYRING_AVAILABLE:
            try:
                token_data = keyring.get_password(TOKEN_KEYRING_SERVICE, self._keyring_user)
                if token_data:
                    self._cached_token = json.loads(token_data)
                    logger.debug("Loaded tokens from keyring")
                    return self._cached_token
            except Exception as e:
                logger.warning(f"Failed to load from keyring: {e}")
        # File fallback
        if self.token_file.exists():
            try:
                self._cached_token = json.loads(self.token_file.read_text())
                logger.debug("Loaded tokens from file")
                return self._cached_token
            except Exception as e:
                logger.warning(f"Failed to load from file: {e}")
        return None

    def _save_tokens(self, token_data: dict):
        """Save tokens securely."""
        token_info = {
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token", ""),
            "expires_in": token_data.get("expires_in", 86400),
            "token_type": token_data.get("token_type", "Bearer"),
            "created_at": datetime.now().isoformat(),
            "expires_at": (datetime.now() + timedelta(seconds=token_data.get("expires_in", 86400))).isoformat()
        }
        self._cached_token = token_info

        # Keyring
        if KEYRING_AVAILABLE:
            try:
                keyring.set_password(TOKEN_KEYRING_SERVICE, self._keyring_user, json.dumps(token_info))
                logger.info("Tokens saved to keyring")
            except Exception as e:
                logger.warning(f"Failed to save to keyring: {e}")

        # File backup
        try:
            self.token_file.parent.mkdir(parents=True, exist_ok=True)
            self.token_file.write_text(json.dumps(token_info, indent=2))
            logger.info(f"Tokens saved to {self.token_file}")
        except Exception as e:
            logger.error(f"Failed to save to file: {e}")

    def _is_token_valid(self, token_info: dict) -> bool:
        """Expiry check with a buffer."""
        try:
            expires_at = datetime.fromisoformat(token_info["expires_at"])
            buffer_time = datetime.now() + timedelta(minutes=TOKEN_EXPIRY_BUFFER_MINUTES)
            return expires_at > buffer_time
        except Exception:
            logger.warning("Could not parse token expiry.")
            return False

    def _validate_token_with_api(self, access_token: str) -> bool:
        """Ping a broker endpoint to confirm access token is alive."""
        for attempt in range(API_RETRY_ATTEMPTS):
            try:
                headers = {
                    "Accept": DEFAULT_ACCEPT_HEADER,
                    "Authorization": f"Bearer {access_token}",
                    "Api-Version": DEFAULT_API_VERSION
                }
                response = requests.get(
                    f"{UPSTOX_BASE_URL}/v2/user/profile",
                    headers=headers,
                    timeout=API_TIMEOUT_SECONDS
                )
                if response.status_code == 200:
                    logger.info("Token validation successful")
                    return True
                elif response.status_code == 401:
                    logger.warning("Token expired or invalid")
                    return False
                else:
                    logger.warning(f"Token validation inconclusive (status: {response.status_code})")
                    return False
            except Exception as e:
                logger.warning(f"Token validation attempt {attempt + 1} failed: {e}")
                if attempt < API_RETRY_ATTEMPTS - 1:
                    time.sleep(API_RETRY_DELAY_SECONDS)
        logger.error("All token validation attempts failed")
        return False

    def _refresh_access_token(self, refresh_token: str) -> str:
        """Try refresh grant, raising explicit error on repeated failure."""
        for attempt in range(API_RETRY_ATTEMPTS):
            try:
                headers = {
                    "Accept": DEFAULT_ACCEPT_HEADER,
                    "Api-Version": DEFAULT_API_VERSION,
                    "Content-Type": DEFAULT_CONTENT_TYPE
                }
                data = {
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": self.api_key,
                    "client_secret": self.secret_key
                }
                response = requests.post(
                    UPSTOX_AUTH_URL,
                    headers=headers,
                    data=data,
                    timeout=API_TIMEOUT_SECONDS
                )
                if response.status_code == 200:
                    token_data = response.json()
                    self._save_tokens(token_data)
                    logger.info("Token refreshed successfully")
                    return token_data["access_token"]
                else:
                    logger.warning(f"Token refresh failed: {response.status_code}")
                    return None
            except Exception as e:
                logger.warning(f"Token refresh attempt {attempt + 1} failed: {e}")
                if attempt < API_RETRY_ATTEMPTS - 1:
                    time.sleep(API_RETRY_DELAY_SECONDS)
        logger.error("All token refresh attempts failed")
        raise TokenRefreshError("Failed to refresh token after retries.")

    def _authenticate_fresh(self) -> str:
        """
        Start the OAuth2 flow using local Flask for callback.
        **PRODUCTION WARNING:** SSL context must be configured for non-localhost/certified callbacks.
        """
        try:
            port = int(self.redirect_url.split(':')[-1].split('/')[0])
        except Exception:
            port = DEFAULT_REDIRECT_PORT

        # SSL context suggestion for prod only
        if 'localhost' in self.redirect_url or '127.0.0.1' in self.redirect_url:
            ssl_ctx = None
        else:
            ssl_ctx = ('cert.pem', 'key.pem')  # Ensure you have real certs in prod

        _run_flask_server(port, ssl_ctx)

        auth_url = (
            f"{UPSTOX_OAUTH_DIALOG_URL}?response_type=code"
            f"&client_id={self.api_key}"
            f"&redirect_uri={self.redirect_url}"
        )
        if not self.no_browser:
            webbrowser.open(auth_url, new=1)
        logger.info(f"If browser doesn't open, visit this link manually: {auth_url}")

        # Wait for callback
        start = time.time()
        while 'code' not in _code_container:
            if time.time() - start > OAUTH_TIMEOUT_SECONDS:
                logger.error("OAuth callback timed out (no auth code received).")
                raise OAuthCallbackTimeout("Timed out waiting for OAuth callback.")
            time.sleep(0.5)
        code = _code_container.pop('code')
        logger.info("Received auth code, exchanging for tokens...")

        # Exchange for tokens
        try:
            resp = requests.post(
                UPSTOX_AUTH_URL,
                headers={
                    'Accept': DEFAULT_ACCEPT_HEADER,
                    'Api-Version': DEFAULT_API_VERSION,
                    'Content-Type': DEFAULT_CONTENT_TYPE
                },
                data={
                    'grant_type': 'authorization_code',
                    'code': code,
                    'client_id': self.api_key,
                    'client_secret': self.secret_key,
                    'redirect_uri': self.redirect_url
                },
                timeout=API_TIMEOUT_SECONDS
            )
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"OAuth token exchange failed: {e}")
            raise AuthError(f"Failed to exchange code for tokens: {e}")

        token_data = resp.json()
        self._save_tokens(token_data)
        return token_data['access_token']

    def clear_stored_tokens(self):
        """Remove all saved credentials—for debug/logout."""
        self._cached_token = None
        # Keyring
        if KEYRING_AVAILABLE:
            try:
                keyring.delete_password(TOKEN_KEYRING_SERVICE, self._keyring_user)
                logger.info("Cleared tokens from keyring")
            except Exception as e:
                logger.warning(f"Failed to clear keyring: {e}")
        # Disk
        if self.token_file.exists():
            try:
                self.token_file.unlink()
                logger.info("Cleared tokens from file")
            except Exception as e:
                logger.warning(f"Failed to clear file: {e}")

# ===== Convenience function =====
def get_access_token_automated(user_id='default', no_browser=False):
    """Get a valid access token, or raise AuthError. Use no_browser=True for CI/headless."""
    try:
        return TokenManager(user_id=user_id, no_browser=no_browser).get_valid_access_token()
    except AuthError as e:
        logger.error(f"Authentication failed: {e}")
        raise
