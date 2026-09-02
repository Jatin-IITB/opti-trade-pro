# src/options_trading/api/tools/option_chain_live.py

from ...config.settings import settings
from ...utils.exceptions import APIError, DataQualityError
from ...utils.http import get_session

URL = settings.upstox_option_chain_url


def fetch_live_option_chain(instrument_key: str, expiry_date: str, access_token: str) -> dict:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "Api-Version": settings.default_api_version,
    }
    params = {"instrument_key": instrument_key, "expiry_date": expiry_date}
    resp = get_session().get(
        URL, headers=headers, params=params, timeout=settings.api_timeout_seconds
    )
    if resp.status_code != 200:
        raise APIError(f"Live option-chain HTTP {resp.status_code}: {resp.text}")

    payload = resp.json()
    if payload.get("status") != "success":
        raise APIError(f"Live option-chain API error: {payload}")

    data = payload.get("data")
    if not data:
        raise DataQualityError("No option-chain data returned")

    return data  # keep raw; service maps to models
