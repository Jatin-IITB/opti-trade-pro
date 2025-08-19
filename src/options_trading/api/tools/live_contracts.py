# src/options_trading/api/tools/live_contracts.py
import requests
import pandas as pd
from ...config.settings import settings
from ...utils.exceptions import APIError, DataQualityError

URL=settings.upstox_option_contracts_url
def fetch_live_option_contracts_df(instrument_key: str, expiry_date: str, access_token: str) -> pd.DataFrame:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "Api-Version": settings.default_api_version,
    }
    params = {"instrument_key": instrument_key, "expiry": expiry_date}
    resp = requests.get(URL, headers=headers, params=params, timeout=settings.api_timeout_seconds)
    if resp.status_code != 200:
        raise APIError(f"Live contracts HTTP {resp.status_code}: {resp.text}")

    payload = resp.json()
    if payload.get("status") != "success":
        raise APIError(f"Live contracts API error: {payload}")

    data = payload.get("data", [])
    if not data:
        raise DataQualityError("No live contracts returned")

    df = pd.DataFrame(data)
    essential = [
        "instrument_key", "trading_symbol", "expiry", "strike_price",
        "instrument_type", "underlying_key", "lot_size", "weekly"
    ]
    for c in essential:
        if c not in df.columns:
            df[c] = pd.NA

    df["expiry"] = pd.to_datetime(df["expiry"])
    df["strike_price"] = df["strike_price"].astype(float)
    df["weekly"] = df["weekly"].astype(bool)
    if df.empty:
        raise DataQualityError("No live contracts in response")

    return df.set_index("instrument_key")
