# api/expiries.py


import requests

from ...config.settings import settings
from ...utils.exceptions import APIError

url = settings.upstox_expired_expiries_url
_EXPIRY_CACHE = {}


def get_expiries(instrument_key: str, access_token: str) -> list[str]:
    """List all available expiry dates for this underlying."""
    if instrument_key in _EXPIRY_CACHE:
        return _EXPIRY_CACHE[instrument_key]
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    resp = requests.get(url, headers=headers, params={"instrument_key": instrument_key})
    payload = resp.json()
    if payload.get("status") != "success":
        raise APIError(f"Expiries API failure: {payload}")
    _EXPIRY_CACHE[instrument_key] = payload["data"]
    return payload["data"]
