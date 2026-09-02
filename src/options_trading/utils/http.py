"""Shared ``requests`` session that verifies against the OS trust store.

TLS-inspecting corporate proxies (Zscaler and similar) terminate the
connection and re-sign the upstream certificate with a private CA. That CA is
installed in the OS trust store, but ``requests`` verifies against certifi's
bundle by default, so every outbound broker call fails the handshake with
``CERTIFICATE_VERIFY_FAILED``.

``requests`` cannot accept an ``ssl.SSLContext`` through ``verify=`` (it takes
only a bool or a bundle path), so the context is installed via a transport
adapter instead. Use :func:`get_session` for all outbound broker HTTP; the
async paths use ``httpx`` with ``verify=truststore.SSLContext(...)`` directly.
"""

from __future__ import annotations

import ssl

import requests
import truststore
from requests.adapters import HTTPAdapter


class TruststoreAdapter(HTTPAdapter):
    """Transport adapter that verifies against the OS certificate store."""

    def init_poolmanager(self, *args: object, **kwargs: object) -> None:
        kwargs["ssl_context"] = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        return super().init_poolmanager(*args, **kwargs)  # type: ignore[arg-type]

    def proxy_manager_for(self, *args: object, **kwargs: object) -> object:
        kwargs["ssl_context"] = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        return super().proxy_manager_for(*args, **kwargs)  # type: ignore[arg-type]


def build_session() -> requests.Session:
    """Create a session that trusts the OS certificate store."""
    session = requests.Session()
    adapter = TruststoreAdapter()
    session.mount("https://", adapter)
    return session


_SESSION: requests.Session | None = None


def get_session() -> requests.Session:
    """Return the process-wide broker HTTP session, creating it on first use.

    Shared so connection pooling and the trust-store adapter apply to every
    outbound call; a bare ``requests.get`` bypasses both.
    """
    global _SESSION
    if _SESSION is None:
        _SESSION = build_session()
    return _SESSION


__all__ = ["TruststoreAdapter", "build_session", "get_session"]
