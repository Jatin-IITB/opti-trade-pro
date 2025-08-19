# api/expiries.py

import requests
from typing import List
from constants import UPSTOX_EXPIRED_EXPIRIES_URL
from api.exceptions import APIError

_EXPIRY_CACHE = {}

def get_expiries(instrument_key: str, access_token: str) -> List[str]:
    """List all available expiry dates for this underlying."""
    if instrument_key in _EXPIRY_CACHE:
        return _EXPIRY_CACHE[instrument_key]
    url = UPSTOX_EXPIRED_EXPIRIES_URL
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
